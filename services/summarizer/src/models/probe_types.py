"""Tier-probe answer (``prompts/tier_probe.txt``) — the early domain/format signal.

The probe runs at transcript-ready on the fast model; its answer picks the
visual tier (``media/visual_tier.py``) and seeds the plan's playbook and hint.
Validation is strict on the fields that steer the pipeline (a registry domain,
a boolean ``has_visual_demo``) and lenient on ``format``, which only refines
the playbook — the classifier it replaces defaulted an unknown format the same way.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, field_validator

from ..shared_config.domain_config import valid_content_tags

DEFAULT_FORMAT = "commentary"

VALID_FORMATS: frozenset[str] = frozenset(
    {
        "tutorial",
        "commentary",
        "reaction",
        "opinion_rant",
        "motivational",
        "interview",
        "lecture",
        "vlog",
        "documentary",
        "walkthrough",
        "podcast",
        "news",
        "news_commentary",
        "entertainment",
        "performance",
        "comparison",
        "story",
        "unboxing",
    }
)


def _enum_id(value: object) -> str:
    return value.strip().lower() if isinstance(value, str) else ""


class TierProbe(BaseModel):
    """One validated probe answer. Extra keys (a stray ``reasoning``) are ignored."""

    model_config = ConfigDict(extra="ignore", frozen=True)

    domain: str
    format: str
    has_visual_demo: bool
    confidence: float

    @field_validator("domain", mode="before")
    @classmethod
    def _registered_domain(cls, value: object) -> str:
        domain = _enum_id(value)
        if domain not in valid_content_tags():
            raise ValueError(f"domain {value!r} is not a registry domain")
        return domain

    @field_validator("format", mode="before")
    @classmethod
    def _known_format(cls, value: object) -> str:
        fmt = _enum_id(value)
        return fmt if fmt in VALID_FORMATS else DEFAULT_FORMAT

    @field_validator("confidence")
    @classmethod
    def _clamp_confidence(cls, value: float) -> float:
        return min(1.0, max(0.0, value))
