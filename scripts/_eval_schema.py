"""Pydantic schema for ``dev/golden-dataset/videos.yaml``.

Validated once at load time so a typo in a per-video assertion fails the
eval before any pipeline spend, not after an hour of runs.

Per-video ``assertions`` are deterministic checks over the assembled API
response (plus the classifier format read from the Langfuse trace). Every
assertion accepts an optional ``xfail`` marker — a reason string, or
``{reason, until}`` naming the task that is expected to fix it: the check
still runs and is reported, but a known, tracked failure does not fail the
gate (a pass is reported as XPASS so the marker gets removed). The gate reads
the markers from this file, so marking a known failure needs no re-run.
"""

from __future__ import annotations

from pathlib import Path
from typing import Annotated, Any, Literal
from urllib.parse import urlparse

from pydantic import BaseModel, ConfigDict, Field, model_validator

DATASET_PATH = Path(__file__).resolve().parent.parent / "dev" / "golden-dataset" / "videos.yaml"

# Golden dataset entries must point at YouTube only. Without this guard a
# malicious PR could swap a URL to an internal host and the eval would
# happily POST it to vie-api — a low-impact SSRF vector that we cut off
# at the script layer.
ALLOWED_VIDEO_HOSTS: frozenset[str] = frozenset(
    {
        "youtube.com",
        "www.youtube.com",
        "m.youtube.com",
        "youtu.be",
    }
)

DEFAULT_QUIZ_COMPONENTS: tuple[str, ...] = ("quiz_arena",)


def is_allowed_video_url(url: str) -> bool:
    """Return ``True`` when ``url`` is an http(s) YouTube URL."""
    if not url or not isinstance(url, str):
        return False
    try:
        parsed = urlparse(url)
    except ValueError:
        return False
    if parsed.scheme not in {"http", "https"}:
        return False
    host = (parsed.hostname or "").lower()
    return host in ALLOWED_VIDEO_HOSTS


# ─── Assertion types ───────────────────────────────────────────────────
class Xfail(BaseModel):
    """A known, tracked failure: ``reason`` and the task expected to fix it."""

    model_config = ConfigDict(extra="forbid")

    reason: str = Field(min_length=1)
    until: str | None = Field(default=None, min_length=1)


class _AssertionBase(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    xfail: Annotated[str, Field(min_length=1)] | Xfail | None = None

    @property
    def xfail_reason(self) -> str | None:
        return self.xfail.reason if isinstance(self.xfail, Xfail) else self.xfail

    @property
    def xfail_until(self) -> str | None:
        return self.xfail.until if isinstance(self.xfail, Xfail) else None


class ExpectedDomain(_AssertionBase):
    """``meta.primaryTag`` must be one of ``values``."""

    type: Literal["expectedDomain"]
    values: list[str] = Field(min_length=1)


class ExpectedFormat(_AssertionBase):
    """The classifier format (Langfuse ``classifier`` generation) is one of ``values``."""

    type: Literal["expectedFormat"]
    values: list[str] = Field(min_length=1)


class ForbiddenComponents(_AssertionBase):
    type: Literal["forbiddenComponents"]
    components: list[str] = Field(min_length=1)


class RequiredComponents(_AssertionBase):
    """Each entry is a component name, or a list of alternatives (any one satisfies it)."""

    type: Literal["requiredComponents"]
    components: list[str | list[str]] = Field(min_length=1)


class QuizAbsentOrLast(_AssertionBase):
    type: Literal["quizAbsentOrLast"]
    components: list[str] = Field(default_factory=lambda: list(DEFAULT_QUIZ_COMPONENTS))


class MinItems(_AssertionBase):
    """Every tab rendering ``component`` holds at least ``min`` items.

    ``field`` names the props list to count; omitted, the longest top-level
    list in the tab's props is counted.
    """

    type: Literal["minItems"]
    component: str
    min_items: int = Field(alias="min", ge=1)
    field: str | None = None


class MinItemsWithField(_AssertionBase):
    """At least ``min`` items of ``component`` carry a non-empty value in any of ``fields``."""

    type: Literal["minItemsWithField"]
    component: str
    fields: list[str] = Field(min_length=1)
    min_items: int = Field(alias="min", ge=1)


class NoTimestampBeyondDuration(_AssertionBase):
    type: Literal["noTimestampBeyondDuration"]
    tolerance_seconds: float = Field(default=2.0, alias="toleranceSeconds", ge=0)


Assertion = Annotated[
    ExpectedDomain
    | ExpectedFormat
    | ForbiddenComponents
    | RequiredComponents
    | QuizAbsentOrLast
    | MinItems
    | MinItemsWithField
    | NoTimestampBeyondDuration,
    Field(discriminator="type"),
]


# ─── Dataset ───────────────────────────────────────────────────────────
class GoldenVideo(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    id: str = Field(pattern=r"^[a-z0-9][a-z0-9-]*$")
    url: str
    domain: str
    format: str
    language: str
    expected_tabs: list[str] = Field(alias="expectedTabs")
    required_components: list[str] = Field(alias="requiredComponents")
    forbidden_components: list[str] = Field(default_factory=list, alias="forbiddenComponents")
    key_content: list[str] = Field(alias="keyContent")
    disabled: bool = False
    # Member of ``run_eval.py --subset quick``: one live video per domain.
    quick: bool = False
    # A placeholder entry (id still to be picked) carries the reason here and
    # must stay disabled so it can never be POSTed.
    todo: str | None = None
    assertions: list[Assertion] = Field(default_factory=list)

    @model_validator(mode="after")
    def _check_runnable(self) -> GoldenVideo:
        if self.todo and not self.disabled:
            raise ValueError(f"{self.id}: an entry with 'todo' must be disabled")
        if self.quick and self.disabled:
            raise ValueError(f"{self.id}: a 'quick' entry must be live")
        if not self.disabled and not is_allowed_video_url(self.url):
            raise ValueError(f"{self.id}: live entry url {self.url!r} is not a YouTube URL")
        return self


class GoldenDataset(BaseModel):
    model_config = ConfigDict(extra="forbid")

    videos: list[GoldenVideo]

    @model_validator(mode="after")
    def _unique_ids(self) -> GoldenDataset:
        seen: set[str] = set()
        for video in self.videos:
            if video.id in seen:
                raise ValueError(f"duplicate golden id: {video.id}")
            seen.add(video.id)
        return self

    @model_validator(mode="after")
    def _one_quick_per_domain(self) -> GoldenDataset:
        """Once any entry is ``quick``, every live domain needs exactly one."""
        if not any(v.quick for v in self.videos):
            return self
        live_domains = {v.domain for v in self.videos if not v.disabled}
        for domain in sorted(live_domains):
            picks = [v.id for v in self.videos if v.quick and v.domain == domain]
            if len(picks) != 1:
                raise ValueError(f"domain {domain!r} needs exactly one quick entry, has {picks}")
        return self


def parse_assertions(record: dict[str, object]) -> list[Assertion]:
    """Validate one raw dataset record and return its typed assertions."""
    return GoldenVideo.model_validate(record).assertions


def read_dataset(path: Path = DATASET_PATH) -> list[dict[str, Any]]:
    """The raw records of ``videos.yaml`` after validating the whole file.

    Raises ``pydantic.ValidationError`` on any schema problem.
    """
    import yaml

    data = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
    GoldenDataset.model_validate(data)
    return list(data["videos"])


def live_records(records: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """Live (not ``disabled``) records by id — the set the gate and noise file measure."""
    return {r["id"]: r for r in records if not r.get("disabled")}
