"""Description analyzer: one fast-model call through ``LLMProvider``.

This module extracts structured data from YouTube video descriptions:
- Links (GitHub, docs, articles, tools)
- Resources (courses, books, named materials)
- Related videos (YouTube links mentioned)
- Timestamps (manual chapter markers)
- Social links (creator's profiles)
"""

import asyncio
import logging
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from src.config import settings
from src.services.llm import LLMService
from src.services.llm_provider import LLMProvider
from src.services.pipeline.pipeline_timing import record_llm_failure
from src.utils.data_helpers import parse_timestamp_to_seconds
from src.utils.json_parsing import parse_json_response
from src.utils.llm_retry import call_llm_with_retry

logger = logging.getLogger(__name__)

PROMPTS_DIR = Path(__file__).parent.parent.parent / "prompts"
_STAGE = "description_analysis"
# Optional analysis, but readers wait for it (chapter tier 2 on the chunked
# path, assembly's meta), so the whole call — both attempts, the backoff and
# any retry-after pause — fits in DESCRIPTION_TOTAL_SECONDS. One retry, for a
# fast transient failure; a slow first attempt leaves no room for a second.
DESCRIPTION_MAX_RETRIES = 1
DESCRIPTION_ATTEMPT_SECONDS = 25.0
DESCRIPTION_TOTAL_SECONDS = 30.0
_MAX_DESCRIPTION_CHARS = 5000


def load_prompt(name: str) -> str:
    """Registry-first prompt loader for description-analyzer prompts."""
    from src.services.pipeline.prompt_builder import load_prompt_text

    path = PROMPTS_DIR / f"{name}.txt"
    return load_prompt_text(path)


@dataclass
class DescriptionLink:
    """A link extracted from the description."""

    url: str
    type: str  # github, documentation, article, tool, course, other
    label: str


@dataclass
class Resource:
    """A named resource from the description."""

    name: str
    url: str


@dataclass
class RelatedVideo:
    """A related YouTube video mentioned in description."""

    title: str
    url: str


@dataclass
class SocialLink:
    """A social media link from description."""

    platform: str  # twitter, discord, github, linkedin, patreon, other
    url: str


@dataclass
class DescriptionTimestamp:
    """A manual chapter marker from the description (e.g. "2:30 Setup")."""

    time: str  # raw "M:SS" | "H:MM:SS" as written by the creator
    seconds: int  # parsed offset into the video
    label: str


@dataclass
class DescriptionAnalysis:
    """Complete analysis of a video description."""

    links: list[DescriptionLink] = field(default_factory=list)
    resources: list[Resource] = field(default_factory=list)
    related_videos: list[RelatedVideo] = field(default_factory=list)
    social_links: list[SocialLink] = field(default_factory=list)
    timestamps: list[DescriptionTimestamp] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary for JSON serialization."""
        return {
            "links": [{"url": l.url, "type": l.type, "label": l.label} for l in self.links],
            "resources": [{"name": r.name, "url": r.url} for r in self.resources],
            "relatedVideos": [{"title": v.title, "url": v.url} for v in self.related_videos],
            "socialLinks": [{"platform": s.platform, "url": s.url} for s in self.social_links],
            "timestamps": [
                {"time": t.time, "seconds": t.seconds, "label": t.label} for t in self.timestamps
            ],
        }

    @property
    def has_content(self) -> bool:
        """Check if any content was extracted."""
        return bool(
            self.links
            or self.resources
            or self.related_videos
            or self.social_links
            or self.timestamps
        )


def _parse_timestamps(raw_items: Any) -> list[DescriptionTimestamp]:
    """Build DescriptionTimestamp entries from raw LLM output.

    Each raw item is expected as {"time": "M:SS"|"H:MM:SS", "label": str}.
    Entries with an unparseable time or empty label are skipped.
    """
    if not isinstance(raw_items, list):
        return []

    result: list[DescriptionTimestamp] = []
    for item in raw_items:
        if not isinstance(item, dict):
            continue
        time_raw = str(item.get("time", "")).strip()
        label = str(item.get("label", "")).strip()
        seconds = parse_timestamp_to_seconds(time_raw)
        if seconds is None or not label:
            continue
        result.append(DescriptionTimestamp(time=time_raw, seconds=seconds, label=label))
    return result


def _parse_analysis(data: dict[str, Any]) -> DescriptionAnalysis:
    """The model's JSON → ``DescriptionAnalysis``; entries without a URL/label are skipped."""
    return DescriptionAnalysis(
        links=[
            DescriptionLink(
                url=l.get("url", ""), type=l.get("type", "other"), label=l.get("label", "")
            )
            for l in data.get("links", [])
            if l.get("url")
        ],
        resources=[
            Resource(name=r.get("name", ""), url=r.get("url", ""))
            for r in data.get("resources", [])
            if r.get("name") and r.get("url")
        ],
        related_videos=[
            RelatedVideo(title=v.get("title", ""), url=v.get("url", ""))
            for v in data.get("relatedVideos", [])
            if v.get("url")
        ],
        social_links=[
            SocialLink(platform=s.get("platform", "other"), url=s.get("url", ""))
            for s in data.get("socialLinks", [])
            if s.get("url")
        ],
        timestamps=_parse_timestamps(data.get("timestamps", [])),
    )


async def _request_analysis(description: str, model: str) -> str | None:
    """The model's raw reply, or None when every attempt failed or the total cap hit."""
    # .replace(), not .format(): the template carries literal JSON braces.
    prompt = load_prompt(_STAGE).replace("{description}", description)
    start = time.monotonic()
    try:
        async with asyncio.timeout(DESCRIPTION_TOTAL_SECONDS):
            return await call_llm_with_retry(
                LLMService(LLMProvider(model=model, fast_model=model)),
                prompt,
                max_tokens=1500,
                timeout=DESCRIPTION_ATTEMPT_SECONDS,
                max_retries=DESCRIPTION_MAX_RETRIES,
                stage_name=_STAGE,
                use_fast_model=True,
            )
    except TimeoutError as e:
        # The cap cancelled an attempt mid-flight, so the provider never saw it
        # fail. Attempts stop at 25 s, so the cap always lands in the last one.
        last_attempt = DESCRIPTION_MAX_RETRIES + 1
        record_llm_failure(
            span=_STAGE, model=model, error=e, start_monotonic=start, attempt=last_attempt
        )
        logger.warning("Description analysis hit its %.0fs cap", DESCRIPTION_TOTAL_SECONDS)
        return None


def _log_analysis(analysis: DescriptionAnalysis) -> None:
    logger.info(
        "Description analysis complete: %d links, %d resources, %d videos, %d social, %d timestamps",
        len(analysis.links),
        len(analysis.resources),
        len(analysis.related_videos),
        len(analysis.social_links),
        len(analysis.timestamps),
    )


async def _analyze_description_async(
    description: str, fast_model: str | None = None
) -> DescriptionAnalysis:
    """Analyze description asynchronously on the fast model.

    Goes through ``call_llm_with_retry`` like every other stage: a Langfuse
    generation on the run's trace, in ``pipeline.timing``, fakeable at the
    provider's single ``acompletion`` seam — with one retry (a dropped
    connection is re-sent at once on top of it) inside a
    ``DESCRIPTION_TOTAL_SECONDS`` cap. Never raises: a failure is an empty
    analysis.

    Args:
        description: The video description text
        fast_model: Optional fast model override. Defaults to settings.llm_fast_model
    """
    if not description or len(description.strip()) < 20:
        logger.debug("Description too short for analysis")
        return DescriptionAnalysis()

    if len(description) > _MAX_DESCRIPTION_CHARS:
        description = description[:_MAX_DESCRIPTION_CHARS] + "..."

    try:
        result_text = await _request_analysis(description, fast_model or settings.llm_fast_model)
        if result_text is None:
            logger.warning("Description analysis failed after retries; continuing without it")
            return DescriptionAnalysis()
        analysis = _parse_analysis(parse_json_response(result_text))
    except Exception as e:
        logger.error("Error analyzing description: %s", e)
        return DescriptionAnalysis()
    _log_analysis(analysis)
    return analysis


async def analyze_description(
    description: str, fast_model: str | None = None
) -> DescriptionAnalysis:
    """
    Analyze a video description to extract structured data via ``LLMProvider``.

    One fast-model call (typically a few seconds, never more than
    ``DESCRIPTION_TOTAL_SECONDS``) that starts at t=0 alongside the transcript.

    Args:
        description: The full video description text
        fast_model: Optional fast model override. Defaults to settings.llm_fast_model

    Returns:
        DescriptionAnalysis with extracted links, resources, timestamps, etc.

    Example:
        analysis = await analyze_description(video_data.description)
        if analysis.has_content:
            # analysis.links contains extracted URLs
            # len(analysis.links) -> 5
    """
    return await _analyze_description_async(description, fast_model)
