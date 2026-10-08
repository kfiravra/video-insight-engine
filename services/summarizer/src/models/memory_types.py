"""Video memory stage types — the fast-model reader that runs in parallel with the plan.

The memory call reads the full transcript once and returns an outline with real
boundaries, the Appendix C evidence booleans, and the hero tldr + takeaways.
Every field is optional downstream: an empty value means "fall back" (no outline,
the plan's evidence only, synthesis's tldr/takeaways).
"""

from __future__ import annotations

from pydantic import BaseModel, Field


class MemoryInput(BaseModel):
    """What the memory call reads: video metadata + the full marked transcript."""

    model_config = {"frozen": True}

    title: str
    channel: str | None = None
    duration: int = Field(description="Video duration in seconds")
    description: str = ""
    # Rendered once per run by ``render_transcript(segments, every=20)`` and
    # shared with the plan, so both readers see byte-identical text.
    transcript: str


class OutlineSection(BaseModel):
    """One outline section; boundaries are whole seconds on the video timeline."""

    model_config = {"frozen": True}

    start: int = Field(ge=0)
    end: int = Field(gt=0)
    title: str


class MemoryResult(BaseModel):
    """Validated, repaired memory output.

    ``outline`` is either a full valid outline (first at 0, last ends at the
    duration, contiguous, every section ≥ 1 min) or empty. ``evidence`` holds
    only the keys the model actually answered — a missing key is "no opinion",
    never ``False``, so it can't make a key doubly-false at reconcile.
    """

    model_config = {"frozen": True}

    outline: list[OutlineSection] = Field(default_factory=list)
    evidence: dict[str, bool] = Field(default_factory=dict)
    tldr: str = ""
    takeaways: list[str] = Field(default_factory=list)

    def is_empty(self) -> bool:
        """True when no field survived validation (the call produced nothing usable)."""
        return not (self.outline or self.evidence or self.tldr or self.takeaways)
