"""Description analyzer: one fast-model call through ``LLMProvider``.

This module extracts structured data from YouTube video descriptions:
- Links (GitHub, docs, articles, tools)
- Resources (courses, books, named materials)
- Related videos (YouTube links mentioned)
- Timestamps (manual chapter markers)
- Social links (creator's profiles)
"""

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import litellm

from src.config import settings
from src.services.llm_provider import LLMProvider
from src.utils.data_helpers import parse_timestamp_to_seconds
from src.utils.json_parsing import parse_json_response

logger = logging.getLogger(__name__)

PROMPTS_DIR = Path(__file__).parent.parent.parent / "prompts"


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


async def _analyze_description_async(
    description: str, fast_model: str | None = None
) -> DescriptionAnalysis:
    """Analyze description asynchronously on the fast model.

    Goes through ``LLMProvider`` like every other stage so the call is a
    Langfuse generation on the run's trace, lands in ``pipeline.timing``,
    and is fakeable at the provider's single ``acompletion`` seam.

    Args:
        description: The video description text
        fast_model: Optional fast model override. Defaults to settings.llm_fast_model
    """
    if not description or len(description.strip()) < 20:
        logger.debug("Description too short for analysis")
        return DescriptionAnalysis()

    # Limit description length to avoid token limits
    max_chars = 5000
    if len(description) > max_chars:
        description = description[:max_chars] + "..."

    try:
        prompt_template = load_prompt("description_analysis")
        # Use .replace() instead of .format() to avoid crashes from literal
        # braces in LLM prompt templates (e.g., JSON examples with {{}})
        prompt = prompt_template.replace("{description}", description)

        model = fast_model or settings.llm_fast_model
        provider = LLMProvider(model=model, fast_model=model)
        result_text = await provider.complete_fast(
            prompt, max_tokens=1500, timeout=30.0, span_name="description_analysis"
        )
        data = parse_json_response(result_text)

        # Parse into dataclasses
        analysis = DescriptionAnalysis(
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

        logger.info(
            "Description analysis complete: %d links, %d resources, %d videos, %d social, %d timestamps",
            len(analysis.links),
            len(analysis.resources),
            len(analysis.related_videos),
            len(analysis.social_links),
            len(analysis.timestamps),
        )

        return analysis

    except litellm.exceptions.APIError as e:
        # LLMProvider already logged this at the error class's severity; the
        # analysis is optional, so it only degrades to an empty result here.
        logger.warning("LLM API error during description analysis: %s", e)
        return DescriptionAnalysis()
    except Exception as e:
        logger.error("Error analyzing description: %s", e)
        return DescriptionAnalysis()


async def analyze_description(
    description: str, fast_model: str | None = None
) -> DescriptionAnalysis:
    """
    Analyze a video description to extract structured data via ``LLMProvider``.

    This is a fast extraction (~1-2 seconds) that runs in parallel with other
    summarization tasks.

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
