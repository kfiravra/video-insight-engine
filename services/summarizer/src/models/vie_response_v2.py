"""VIEResponse v2 — component-addressed tab layout.

Each tab specifies a component name (registry key) and pre-assembled props.
The frontend renders: registry[tab.component](tab.props).

Mirrors ``packages/types/src/vie-response.ts`` (``TabEntry``/``TabAttachment``).
Field parity with the TS side is enforced by
``tests/test_ts_pydantic_parity.py`` — undeclared fields silently drop on
``model_dump``, which is exactly the class of bug that test exists to catch.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field


class CrossTabLink(BaseModel):
    """A link from one tab to another."""

    target_tab: str = Field(alias="targetTab")
    label: str

    model_config = {"populate_by_name": True}


class TabAttachment(BaseModel):
    """Secondary-tier component attached above/below a tab's primary interactive.

    Mirrors the TS ``TabAttachment`` — attachments enrich sparse tabs (frame
    strip, quick quiz, tip) or break up dense ones (summary header). They are
    never standalone tabs.
    """

    model_config = {"populate_by_name": True}

    slot: Literal["top", "bottom"]
    component: str
    props: dict[str, Any] = {}
    size: Literal["banner", "strip"] | None = None


class TabEntry(BaseModel):
    """A single tab with component-addressed props."""

    model_config = {"populate_by_name": True}

    id: str
    label: str
    emoji: str = ""
    component: str  # Registry key (e.g., "spot_explorer", "checklist")
    props: dict[str, Any] = {}
    goal: str = ""
    cross_tab_links: list[CrossTabLink] = Field([], alias="crossTabLinks")
    attachments: list[TabAttachment] | None = None
    # Set by the degrade-never-drop ladder when a richer component's assembler
    # couldn't build from the data. Telemetry/debug only.
    degraded_from: str | None = Field(None, alias="degradedFrom")


class VIEResponseMeta(BaseModel):
    """Metadata about the video and its analysis.

    Mirrors TS ``VIEResponseMeta`` (parity-checked by
    ``tests/test_ts_pydantic_parity.py``); ``title``/``duration`` are extra
    Python-side fields carried by the assembled meta dict.
    """

    model_config = {"populate_by_name": True}

    video_id: str = Field("", alias="videoId")
    video_title: str = Field("", alias="videoTitle")
    title: str = ""
    creator: str = ""
    duration: int | None = None
    content_tags: list[str] = Field([], alias="contentTags")
    modifiers: list[str] = []
    primary_tag: str = Field("learning", alias="primaryTag")
    user_goal: str = Field("", alias="userGoal")
    tldr: str | None = None
    key_takeaways: list[str] | None = Field(None, alias="keyTakeaways")
    master_summary: str | None = Field(None, alias="masterSummary")
    seo_description: str | None = Field(None, alias="seoDescription")
    # The hero rewrite's own fields (output-quality 2.2); absent on older rows.
    brief: str | None = None
    worth_it_for: str | None = Field(None, alias="worthItFor")
    skip_if: str | None = Field(None, alias="skipIf")
    reading_minutes: int | None = Field(None, alias="readingMinutes")
    language: str | None = None
    is_rtl: bool | None = Field(None, alias="isRTL")
    # Partial-result flag (dropped extraction batches / critical coverage) —
    # set True-only by the assembly phase; drives the FE retry affordance.
    degraded: bool | None = None


class VIEResponseV2(BaseModel):
    """The new component-addressed VIEResponse."""

    model_config = {"populate_by_name": True}

    meta: VIEResponseMeta
    tabs: list[TabEntry] = []
