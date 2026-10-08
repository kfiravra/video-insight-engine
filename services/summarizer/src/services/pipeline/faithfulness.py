"""LLM-as-Judge faithfulness check.

Samples a deterministic subset of extracted items, asks a Haiku-tier LLM
whether each claim is supported by the transcript, and logs an aggregate
score onto the active Langfuse trace.

Design notes
------------
- **Non-blocking by contract.** The judge runs as a fire-and-forget task
  attached to the pipeline trace. Failures are logged at debug level — the
  user-facing video output is never delayed by a faithfulness check.
- **Deterministic sampling.** The sample seed is derived from the
  ``youtube_id`` so the same video always gets the same items checked.
  That keeps day-to-day variance attributable to model drift rather than
  random sampling noise.
- **Cheap.** Haiku-tier judge, 20% sample, capped at 6 items per video.
  Per-video cost ~$0.005.
- **Claim-localized context.** Transcripts longer than the judged-context
  budget are not head-sliced — each claim is judged against the transcript
  windows that share the most (IDF-weighted) vocabulary with it, packed
  into the same budget in document order. Pure-python lexical scoring; no
  extra dependencies, no extra LLM calls.
"""

from __future__ import annotations

import asyncio
import json
import logging
import math
import random
import re
from collections import Counter
from dataclasses import dataclass
from typing import Any

from src.config import settings
from src.services.llm import LLMService
from src.services.observability import log_score

logger = logging.getLogger(__name__)


_MAX_SAMPLES_PER_VIDEO = 6
_JUDGE_TIMEOUT_SECONDS = 20.0
# Judged-context budget passed to the judge LLM (call_llm_fast path).
#
# Sized for the default fast tier — Anthropic Haiku-4.5, 200K-token context;
# 80K chars ≈ 20K tokens leaves ample room for the judge prompt + claim.
# Transcripts within the budget pass through whole. Longer transcripts are
# NOT head-sliced (a 3h+ video head-slice meant tail claims were judged
# against text that could not contain them → false hallucination verdicts);
# instead the budget is filled with the transcript windows most lexically
# relevant to the claim being judged — see `_select_claim_context`.
#
# WARNING for operators: this constant is coupled to the judge model's
# context window. If LLM_FAST_MODEL or a per-stage faithfulness override
# routes the judge through a smaller-context model (e.g. an 8K-context
# legacy model), this budget will overflow the model's prompt limit and
# every judge call will error out. The diagnostic log in
# `run_faithfulness_check` emits `truncated=True` when the input transcript
# exceeds the budget; watch for that signal when swapping models.
_TRANSCRIPT_BUDGET_CHARS = 80_000
_CLAIM_BUDGET_CHARS = 400


_JUDGE_PROMPT = (
    "You are a fact-checker. Given a transcript excerpt and an extracted claim, "
    "decide whether the claim is supported by the transcript.\n\n"
    "Reply in this exact JSON format:\n"
    '{{"grounded": true|false, "evidence": "quoted span ≤25 words or empty"}}\n\n'
    'Be strict: "grounded" must be true only if the transcript explicitly '
    "states the claim. Paraphrases are fine; inferences are not.\n"
    "The excerpt may end with a <visual_annotations> block (what the video "
    "shows on screen at [m:ss]); a claim supported there counts as supported.\n\n"
    "TRANSCRIPT:\n{transcript}\n\n"
    "CLAIM:\n{claim}\n"
)


@dataclass(frozen=True)
class FaithfulnessReport:
    """Aggregate result for one pipeline run."""

    sample_size: int
    grounded_count: int
    score: float  # 0.0–1.0, fraction grounded

    @property
    def passed(self) -> bool:
        return self.score >= 0.7


# ─── Claim flattening ───────────────────────────────────────────────────
def _flatten_claims(extraction_data: dict[str, Any]) -> list[str]:
    """Pull short string claims out of an extraction blob.

    Walks the nested dict, collecting strings under known leaf fields
    ("text", "description", "definition", "name") that look meaningful
    (≥20 chars, ≤_CLAIM_BUDGET_CHARS). Skips empty strings and obvious
    boilerplate.
    """
    keep_keys = {"text", "description", "definition", "name", "summary", "claim"}
    out: list[str] = []

    def _walk(node: Any) -> None:
        if isinstance(node, dict):
            for k, v in node.items():
                if k in keep_keys and isinstance(v, str):
                    cleaned = v.strip()
                    if 20 <= len(cleaned) <= _CLAIM_BUDGET_CHARS:
                        out.append(cleaned)
                else:
                    _walk(v)
        elif isinstance(node, list):
            for item in node:
                _walk(item)

    _walk(extraction_data)
    # Deduplicate while preserving order.
    seen: set[str] = set()
    unique: list[str] = []
    for c in out:
        if c not in seen:
            seen.add(c)
            unique.append(c)
    return unique


def _sample_claims(claims: list[str], rate: float, youtube_id: str) -> list[str]:
    """Deterministic sample of claims at the given rate, capped per video."""
    if rate <= 0 or not claims:
        return []
    rng = random.Random(youtube_id)
    pick_count = min(_MAX_SAMPLES_PER_VIDEO, max(1, int(round(len(claims) * rate))))
    return rng.sample(claims, k=min(pick_count, len(claims)))


# ─── Claim-localized context selection ──────────────────────────────────
# Selection granularity. 4K chars ≈ 4 minutes of speech — wide enough that a
# claim and its surrounding evidence land in one window, narrow enough that
# a 3h transcript (~200K chars) yields ~50 rankable windows.
_WINDOW_CHARS = 4_000
_GAP_MARKER = "\n[...]\n"
_MIN_TOKEN_LEN = 3
# Tiny English stopword list — enough to stop function words from dominating
# the overlap score. Deliberately small: over-filtering hurts non-English
# transcripts, where these strings rarely occur anyway.
_STOPWORDS = frozenset(
    {
        "the",
        "and",
        "for",
        "that",
        "this",
        "with",
        "from",
        "are",
        "was",
        "were",
        "has",
        "have",
        "had",
        "not",
        "but",
        "you",
        "your",
        "they",
        "their",
        "there",
        "what",
        "which",
        "when",
        "where",
        "who",
        "how",
        "why",
        "can",
        "could",
        "will",
        "would",
        "should",
        "about",
        "into",
        "than",
        "then",
        "them",
        "these",
        "those",
        "its",
        "his",
        "her",
        "she",
        "him",
        "our",
        "out",
        "all",
        "also",
        "just",
        "like",
        "more",
        "most",
        "some",
        "such",
        "very",
        "been",
        "being",
        "because",
        "over",
        "under",
        "after",
        "before",
        "between",
        "while",
        "during",
        "each",
        "other",
    }
)


@dataclass(frozen=True)
class _TranscriptWindow:
    """One contiguous slice of the transcript, in document order."""

    index: int
    offset: int
    text: str
    token_counts: Counter[str]


def _tokenize(text: str) -> list[str]:
    """Lowercase word tokens, minus stopwords and very short tokens."""
    return [
        tok
        for tok in re.findall(r"[^\W_]+", text.lower(), flags=re.UNICODE)
        if len(tok) >= _MIN_TOKEN_LEN and tok not in _STOPWORDS
    ]


def _split_windows(transcript: str, window_chars: int = _WINDOW_CHARS) -> list[_TranscriptWindow]:
    """Split the transcript into ordered ~window_chars slices.

    Boundaries snap forward to the next whitespace so words (and inline
    timestamps) are never cut mid-token. Ordering and offsets preserve the
    original document layout.
    """
    if not transcript:
        return []
    windows: list[_TranscriptWindow] = []
    pos = 0
    length = len(transcript)
    while pos < length:
        end = min(pos + window_chars, length)
        if end < length:
            boundary = transcript.find(" ", end)
            end = boundary if boundary != -1 else length
        chunk = transcript[pos:end]
        windows.append(
            _TranscriptWindow(
                index=len(windows),
                offset=pos,
                text=chunk,
                token_counts=Counter(_tokenize(chunk)),
            )
        )
        pos = end
    return windows


def _score_window(
    window: _TranscriptWindow,
    claim_tokens: frozenset[str],
    doc_freq: dict[str, int],
    n_windows: int,
) -> float:
    """BM25-flavored lexical overlap between a claim and one window.

    IDF weighting keeps rare, claim-specific terms decisive; the tf
    saturation term stops one repeated common word from outranking a
    window that matches several distinct claim terms.
    """
    score = 0.0
    for token in claim_tokens:
        tf = window.token_counts.get(token, 0)
        if tf == 0:
            continue
        df = doc_freq[token]
        idf = math.log(1.0 + (n_windows - df + 0.5) / (df + 0.5))
        score += idf * (tf / (tf + 1.2))
    return score


def _join_selected_windows(selected: list[_TranscriptWindow]) -> str:
    """Concatenate windows in document order, marking elided gaps.

    Adjacent windows are contiguous transcript slices (each chunk already
    carries its boundary whitespace), so they concatenate directly —
    inserting anything would corrupt text spanning the boundary.
    """
    ordered = sorted(selected, key=lambda w: w.index)
    parts: list[str] = []
    prev_index: int | None = None
    for window in ordered:
        if prev_index is not None and window.index != prev_index + 1:
            parts.append(_GAP_MARKER)
        parts.append(window.text)
        prev_index = window.index
    return "".join(parts)


def _select_claim_context(
    transcript: str,
    claim: str,
    budget: int = _TRANSCRIPT_BUDGET_CHARS,
    windows: list[_TranscriptWindow] | None = None,
) -> str:
    """Build the judged context for one claim.

    Short transcripts (≤ budget) pass through whole. Longer transcripts are
    split into ordered windows; the top-scoring windows for this claim are
    packed into the budget and re-joined in document order (gaps marked), so
    a claim from hour 3 is judged against hour-3 text instead of the head.
    Falls back to the head slice when the claim shares no vocabulary with
    the transcript (no signal to localize on).
    """
    if not transcript:
        return ""
    if len(transcript) <= budget:
        return transcript

    if windows is None:
        windows = _split_windows(transcript)
    claim_tokens = frozenset(_tokenize(claim))
    if not claim_tokens:
        return _truncate(transcript, budget)

    n_windows = len(windows)
    doc_freq = {token: sum(1 for w in windows if token in w.token_counts) for token in claim_tokens}
    scored = [(w, _score_window(w, claim_tokens, doc_freq, n_windows)) for w in windows]
    # Highest score first; document order breaks ties deterministically.
    scored.sort(key=lambda pair: (-pair[1], pair[0].index))

    selected_indices: set[int] = set()
    used = 0
    for window, score in scored:
        if score <= 0.0:
            break
        if used + len(window.text) > budget:
            break
        selected_indices.add(window.index)
        used += len(window.text)

    if not selected_indices:
        return _truncate(transcript, budget)

    # Neighbor expansion: an evidence sentence can straddle a window boundary,
    # and its spillover half may share no vocabulary with the claim (e.g. only
    # a trailing clause). Pulling in the immediate neighbors of each scoring
    # window keeps such evidence intact and gives the judge surrounding
    # context. Iterating in score order keeps the packing deterministic.
    for window, score in scored:
        if score <= 0.0:
            break
        for neighbor_idx in (window.index - 1, window.index + 1):
            if neighbor_idx < 0 or neighbor_idx >= n_windows:
                continue
            if neighbor_idx in selected_indices:
                continue
            neighbor_len = len(windows[neighbor_idx].text)
            if used + neighbor_len > budget:
                continue
            selected_indices.add(neighbor_idx)
            used += neighbor_len

    return _join_selected_windows([windows[i] for i in sorted(selected_indices)])


# ─── Judging ────────────────────────────────────────────────────────────
def _truncate(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    return text[:limit] + "\n[truncated]"


def _parse_judge_response(raw: str) -> bool | None:
    """Return ``True`` if grounded, ``False`` if not, ``None`` if unparseable."""
    if not raw:
        return None
    # Find the first {...} block — judge output is occasionally wrapped in prose.
    match = re.search(r"\{[^{}]*\}", raw, flags=re.DOTALL)
    payload = match.group(0) if match else raw
    try:
        data = json.loads(payload)
    except (json.JSONDecodeError, TypeError):
        return None
    grounded = data.get("grounded")
    if isinstance(grounded, bool):
        return grounded
    if isinstance(grounded, str):
        return grounded.lower() == "true"
    return None


async def _judge_one(llm_service: LLMService, context: str, claim: str) -> bool | None:
    """Ask the judge about one claim against its pre-selected context.

    ``context`` is the claim-localized transcript excerpt built by
    ``_select_claim_context`` (already within budget). Returns ``None``
    on judge failure.
    """
    from src.utils.llm_retry import call_llm_with_retry

    # Defensive backstop only — selection already packs within the budget;
    # the slack absorbs the joins' gap markers.
    prompt = _JUDGE_PROMPT.format(
        transcript=_truncate(context, _TRANSCRIPT_BUDGET_CHARS + 1_024),
        claim=_truncate(claim, _CLAIM_BUDGET_CHARS),
    )
    try:
        raw = await call_llm_with_retry(
            llm_service,
            prompt,
            max_tokens=256,
            timeout=_JUDGE_TIMEOUT_SECONDS,
            max_retries=1,
            stage_name="faithfulness",
            use_fast_model=True,
            json_mode=True,
        )
    except Exception as exc:  # noqa: BLE001 — judge errors must not crash pipeline
        logger.debug("Faithfulness judge call failed: %s", exc)
        return None
    verdict = _parse_judge_response(raw or "")
    # DEBUG-level: per-claim raw bodies are noisy at 6 lines per video and
    # carry transcript-derived text that shouldn't routinely ship to log
    # aggregators. The one-line run summary (in run_faithfulness_check) is
    # the production-visible signal; enable DEBUG locally to dig into a
    # specific run.
    logger.debug(
        "[faithfulness] claim=%r verdict=%s raw=%r",
        claim[:120],
        verdict,
        (raw or "")[:200],
    )
    return verdict


# ─── Orchestrator ───────────────────────────────────────────────────────
async def run_faithfulness_check(
    *,
    llm_service: LLMService,
    transcript: str,
    extraction_data: dict[str, Any],
    youtube_id: str,
    sample_rate: float | None = None,
) -> FaithfulnessReport | None:
    """Score the extraction against the transcript, log to Langfuse.

    Returns the aggregate report (handy for the pipeline summary log) or
    ``None`` when sampling is disabled / nothing to score. Never raises.
    """
    rate = sample_rate if sample_rate is not None else settings.LANGFUSE_FAITHFULNESS_SAMPLE_RATE
    if rate <= 0:
        return None

    claims = _flatten_claims(extraction_data)
    sample = _sample_claims(claims, rate, youtube_id)
    if not sample:
        logger.debug("Faithfulness: no claims to sample for %s", youtube_id)
        return None

    transcript_len = len(transcript)
    transcript_truncated = transcript_len > _TRANSCRIPT_BUDGET_CHARS
    logger.info(
        "[faithfulness] video=%s claims_total=%d sample_size=%d "
        "transcript_chars=%d window_chars=%d truncated=%s",
        youtube_id,
        len(claims),
        len(sample),
        transcript_len,
        min(transcript_len, _TRANSCRIPT_BUDGET_CHARS),
        transcript_truncated,
    )

    # Over-budget transcripts get claim-localized contexts (top lexical
    # windows per claim, document order preserved). Windows are split and
    # token-counted once and shared across the sampled claims.
    shared_windows = _split_windows(transcript) if transcript_truncated else None
    contexts = [
        _select_claim_context(transcript, claim, windows=shared_windows) for claim in sample
    ]

    # ``return_exceptions=True`` so one unexpected raise inside ``_judge_one``
    # (e.g. a transport error escaping ``call_llm_with_retry``) only loses that
    # one verdict — never the whole sampling run.
    results = await asyncio.gather(
        *(_judge_one(llm_service, ctx, c) for ctx, c in zip(contexts, sample)),
        return_exceptions=True,
    )
    judged = [r for r in results if isinstance(r, bool)]
    if not judged:
        logger.debug("Faithfulness: judge produced no usable verdicts for %s", youtube_id)
        return None

    grounded_count = sum(1 for v in judged if v is True)
    score = grounded_count / len(judged)
    report = FaithfulnessReport(
        sample_size=len(judged),
        grounded_count=grounded_count,
        score=round(score, 3),
    )

    log_score(
        "faithfulness",
        report.score,
        comment=f"{report.grounded_count}/{report.sample_size} grounded "
        f"({len(sample) - len(judged)} judge errors)",
    )
    if not report.passed:
        logger.warning(
            "Low faithfulness score for %s: %.2f (%d/%d grounded)",
            youtube_id,
            report.score,
            report.grounded_count,
            report.sample_size,
        )
    return report
