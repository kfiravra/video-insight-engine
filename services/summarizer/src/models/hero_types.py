"""Hero rewrite types — the page's overview written from the merged extraction (output-quality 2.1).

One Sonnet call at text-groups-done (D1) writes the hero the page shows at
``complete``: the thesis, a 3–5 sentence brief, the 3 / 5 / 7 key takeaways with
the timestamp code copied from the extraction item each one cites, the "worth it
for" / "skip if" lines (D18) and the SEO line. The reading time is a property
of the shipped page, so code computes it after assembly (``reading_minutes``).
"""

from __future__ import annotations

from pydantic import BaseModel, Field


class HeroTakeaway(BaseModel):
    """One key takeaway: a claim and the second of the video it rests on."""

    model_config = {"frozen": True}

    text: str
    timestamp: int = Field(ge=0, description="Seconds from the video start")


class HeroRewriteResult(BaseModel):
    """The validated hero. ``tldr``, ``brief`` and the takeaways are always set;
    an optional line that failed validation twice is ``""`` (dropped, D18).

    ``reading_minutes`` stays None here: the page's reading time is counted
    over the shipped hero + tabs once assembly is final.
    """

    model_config = {"frozen": True, "populate_by_name": True}

    tldr: str
    brief: str
    key_takeaways: list[HeroTakeaway] = Field(alias="keyTakeaways", min_length=1)
    worth_it_for: str = Field("", alias="worthItFor")
    skip_if: str = Field("", alias="skipIf")
    seo_description: str = Field("", alias="seoDescription")
    reading_minutes: int | None = Field(None, alias="readingMinutes", ge=1)
