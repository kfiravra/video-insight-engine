"""Claim-localized transcript context for the faithfulness judge.

Transcripts within the judged-context budget pass through whole. Longer ones are
not head-sliced: each claim is judged against the transcript windows that share
the most (IDF-weighted) vocabulary with it, packed into the same budget in
document order with elided gaps marked. Pure-python lexical scoring; no extra
dependencies, no extra LLM calls. Split out of ``faithfulness.py`` (output-quality
2.4) to keep that module under the 500-line cap.
"""

from __future__ import annotations

import math
import re
from collections import Counter
from dataclasses import dataclass

# Judged-context budget passed to the judge LLM (call_llm_fast path).
#
# Sized for a 128K+-token judge (gpt-6-luna; gpt-4o-mini, the blank-setting
# fast tier); 80K chars ≈ 20K tokens leaves ample room for the prompt + claim.
# Transcripts within the budget pass through whole. Longer transcripts are
# NOT head-sliced (a 3h+ video head-slice meant tail claims were judged
# against text that could not contain them → false hallucination verdicts);
# instead the budget is filled with the transcript windows most lexically
# relevant to the claim being judged — see `_select_claim_context`.
#
# WARNING for operators: this constant is coupled to the judge model's
# context window. If LLM_JUDGE_MODEL (or, when it is blank, LLM_FAST_MODEL)
# routes the judge through a smaller-context model (e.g. an 8K-context
# legacy model), this budget will overflow the model's prompt limit and
# every judge call will error out. The diagnostic log in
# `run_faithfulness_check` emits `truncated=True` when the input transcript
# exceeds the budget; watch for that signal when swapping models.
_TRANSCRIPT_BUDGET_CHARS = 80_000


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


def _truncate(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    return text[:limit] + "\n[truncated]"
