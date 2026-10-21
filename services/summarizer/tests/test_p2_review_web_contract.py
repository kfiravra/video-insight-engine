"""Phase-2 review fixes, lane 4 (web + api + types): ``gates/review-p2-C.md``.

- C3: the corrected ``synthesis_complete`` after the late group is marked ``final`` —
  consumers replace their copy with it (a field the scrub emptied arrives empty), where
  every earlier emission merges. The memory-done and synthesis-done ones never carry it.
"""

from __future__ import annotations

import json
from types import SimpleNamespace
from typing import Any

import pytest

from src.models.sse_events import SSEEventValidationError, validate_sse_event
from src.services.pipeline.phases.synthesis import settle_tab_references, synthesis_event

_HERO = {
    "tldr": "Agents write the code now.",
    "keyTakeaways": ["Review is the job. [1:02]"],
    "masterSummary": "It opens on a bet. It ends on a promise.",
    "seoDescription": "A keynote on agents writing the code.",
    "brief": "It opens on a bet. It ends on a promise.",
    "worthItFor": "Leads deciding how far to let agents in.",
    "skipIf": "You want a how-to: no code is written on stage.",
}


def _tabs() -> list[dict[str, Any]]:
    return [
        {"id": "overview", "label": "Overview", "component": "overview", "props": {"data": {}}},
        {
            "id": "ideas",
            "label": "Key Ideas",
            "component": "concept_canvas",
            "props": {"items": [{"name": "Agents", "description": "They write the code."}]},
        },
    ]


def _settle(hero: dict[str, Any]) -> dict[str, Any] | None:
    tabs = _tabs()
    ctx = SimpleNamespace(
        assembled_meta=dict(hero),
        assembled_tabs=tabs,
        triage_dict={"tabs": []},
        synthesis_dict=dict(hero),
    )
    event = settle_tab_references(ctx, {"tabs": tabs, "dropped": []})  # type: ignore[arg-type]
    return None if event is None else json.loads(event.removeprefix("data: "))


class TestFinalSuperset:
    def test_should_mark_the_corrected_superset_final_when_the_late_group_changed_the_hero(
        self,
    ) -> None:
        payload = _settle(_HERO)

        assert payload is not None and payload["final"] is True

    def test_should_carry_the_reading_time_on_the_final_superset_when_the_hero_wrote_it(
        self,
    ) -> None:
        payload = _settle(_HERO)

        assert payload is not None and payload["readingMinutes"] >= 1

    def test_should_send_no_final_superset_when_the_settled_hero_is_unchanged(self) -> None:
        synthesis_path = {key: _HERO[key] for key in ("tldr", "keyTakeaways", "masterSummary")}

        assert _settle({**synthesis_path, "seoDescription": ""}) is None

    def test_should_leave_the_synthesis_done_superset_unmarked(self) -> None:
        assert "final" not in synthesis_event(_HERO)


class TestFinalFlagContract:
    def test_should_accept_a_final_superset_whose_scrub_emptied_fields(self) -> None:
        emptied = {**_HERO, "tldr": "", "keyTakeaways": [], "worthItFor": "", "final": True}

        model = validate_sse_event("synthesis_complete", emptied)

        assert getattr(model, "final", None) is True

    def test_should_reject_a_final_flag_that_is_not_a_boolean(self) -> None:
        with pytest.raises(SSEEventValidationError):
            validate_sse_event("synthesis_complete", {**_HERO, "final": "maybe"})
