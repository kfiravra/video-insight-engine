"""Tier probe — the early, cheap domain/format/visual-demo call (brief Appendix B.1).

Runs at transcript-ready on Haiku 4.5 (D21) at temperature 0 and reads only
metadata plus three short clean transcript windows, so it answers in about a
second. The frames branch waits for it at most 3 s before Step 6b
(``media/visual_tier.resolve_tier``); the plan uses it for the playbook and a
hint line. Any failure returns ``None`` — every consumer has a fallback (the
metadata tier, no hint), so the probe never raises into the pipeline.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

from pydantic import ValidationError

from ...config import settings
from ...models.probe_types import TierProbe
from ...utils.json_parsing import parse_json_response
from ...utils.llm_retry import call_llm_with_retry
from .pipeline_helpers import sanitize_for_prompt
from .prompt_builder import load_prompt_text

if TYPE_CHECKING:
    from ...services.llm import LLMService
    from ...services.video.youtube import VideoData

logger = logging.getLogger(__name__)

PROMPT_PATH = Path(__file__).parent.parent.parent / "prompts" / "tier_probe.txt"
STAGE_NAME = "tier_probe"
PROBE_MAX_TOKENS = 80
# Haiku answers in ~1 s (A/B max 1.6 s). One attempt, no retry: a stalled
# probe is cheaper to drop (metadata tier, no hint) than to wait out again.
PROBE_TIMEOUT_SECONDS = 4.0
WINDOW_CHARS = 700
DESCRIPTION_CHARS = 500
MAX_TAGS = 15
_NO_TRANSCRIPT = "(no transcript)"


@dataclass(frozen=True)
class TierProbeInput:
    """Everything Appendix B.1 feeds the probe."""

    title: str
    channel: str
    duration: int
    youtube_category: str | None
    description: str
    transcript: str
    tags: list[str] = field(default_factory=list)

    @classmethod
    def from_video(cls, video_data: VideoData, transcript: str) -> TierProbeInput:
        """Build the input from the metadata phase's ``VideoData`` + the clean transcript."""
        context = video_data.context
        return cls(
            title=video_data.title or "",
            channel=video_data.channel or "",
            duration=video_data.duration or 0,
            youtube_category=context.youtube_category if context else None,
            description=video_data.description or "",
            transcript=transcript,
            tags=list(context.tags) if context else [],
        )


# ─── Transcript windows ──────────────────────────────────────────────────────


def _window(text: str, start: int, size: int) -> str:
    """Slice ``size`` chars from ``start`` and trim partial words at both edges."""
    start = max(0, min(start, len(text) - size))
    chunk = text[start : start + size]
    if start > 0 and " " in chunk:
        chunk = chunk.split(" ", 1)[1]
    if start + size < len(text) and " " in chunk:
        chunk = chunk.rsplit(" ", 1)[0]
    return chunk.strip()


def transcript_windows(transcript: str, size: int = WINDOW_CHARS) -> tuple[str, str, str]:
    """Start/middle/end windows of the whitespace-collapsed transcript.

    A short transcript is split, never duplicated. The probe reads
    ``clean_text``, which never carries frame annotations (1c.2 renders them
    into their own block), so there is nothing to strip.
    """
    clean = " ".join(transcript.split())
    if len(clean) <= 3 * size:
        thirds = [clean[i * len(clean) // 3 : (i + 1) * len(clean) // 3] for i in range(3)]
        return thirds[0].strip(), thirds[1].strip(), thirds[2].strip()
    mid_start = len(clean) // 2 - size // 2
    return (
        _window(clean, 0, size),
        _window(clean, mid_start, size),
        _window(clean, len(clean), size),
    )


# ─── Prompt ──────────────────────────────────────────────────────────────────


def render_tier_probe_prompt(template: str, probe_input: TierProbeInput) -> str:
    """Fill the B.1 template; every user-controlled value is sanitized and capped."""
    start, mid, end = transcript_windows(probe_input.transcript)
    duration = probe_input.duration
    values = {
        "{title}": sanitize_for_prompt(probe_input.title, max_len=200),
        "{channel}": sanitize_for_prompt(probe_input.channel or "Unknown", max_len=100),
        "{duration_minutes}": str(round(duration / 60)) if duration > 0 else "unknown",
        "{youtube_category}": sanitize_for_prompt(
            probe_input.youtube_category or "unknown", max_len=60
        ),
        "{tags}": sanitize_for_prompt(", ".join(probe_input.tags[:MAX_TAGS]) or "none"),
        "{description}": sanitize_for_prompt(
            probe_input.description.strip(), max_len=DESCRIPTION_CHARS
        )
        or "none",
        "{window_start}": sanitize_for_prompt(start, max_len=WINDOW_CHARS) or _NO_TRANSCRIPT,
        "{window_mid}": sanitize_for_prompt(mid, max_len=WINDOW_CHARS) or _NO_TRANSCRIPT,
        "{window_end}": sanitize_for_prompt(end, max_len=WINDOW_CHARS) or _NO_TRANSCRIPT,
    }
    prompt = template
    for placeholder, value in values.items():
        prompt = prompt.replace(placeholder, value)
    return prompt


# ─── Answer ──────────────────────────────────────────────────────────────────


def parse_tier_probe(raw: str) -> TierProbe | None:
    """Validate a probe answer; ``None`` when it holds no valid probe object.

    Haiku ignores ``json_object`` (gate-0 A/B): it fences the JSON and may
    append reasoning until ``max_tokens``. The first JSON object wins, so
    fences and trailing prose are tolerated; a truncated object fails the
    required-keys check.
    """
    data = parse_json_response(raw)
    if not data:
        logger.warning("Tier probe answer holds no JSON object: %.200s", raw)
        return None
    try:
        return TierProbe.model_validate(data)
    except ValidationError as exc:
        logger.warning("Tier probe answer rejected: %s", exc.errors(include_url=False))
        return None


async def run_tier_probe(probe_input: TierProbeInput, llm_service: LLMService) -> TierProbe | None:
    """One temperature-0 probe call → validated answer, or ``None`` on any failure."""
    try:
        prompt = render_tier_probe_prompt(load_prompt_text(PROMPT_PATH), probe_input)
        raw = await call_llm_with_retry(
            llm_service,
            prompt,
            max_tokens=PROBE_MAX_TOKENS,
            timeout=PROBE_TIMEOUT_SECONDS,
            max_retries=0,
            stage_name=STAGE_NAME,
            json_mode=True,
            use_fast_model=True,
            model_override=settings.get_stage_model(STAGE_NAME),
            temperature=0.0,
        )
    except Exception as exc:  # noqa: BLE001 — advisory call; every consumer has a fallback
        logger.warning("Tier probe failed: %s", exc)
        return None
    if not raw:
        return None
    probe = parse_tier_probe(raw)
    if probe is not None:
        logger.info(
            "Tier probe: domain=%s format=%s has_visual_demo=%s confidence=%.2f",
            probe.domain,
            probe.format,
            probe.has_visual_demo,
            probe.confidence,
        )
    return probe
