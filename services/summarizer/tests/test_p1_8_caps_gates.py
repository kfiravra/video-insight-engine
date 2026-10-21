"""p1.8 — caps and gates (R9, R7), Kfir's 2026-10-21 decisions, and D8 single-sourcing.

The Moments track is sized strictly by content-model §9 (``momentCapsByDuration``,
no slack: a described track up to its band's max) and its density gate wants
what each moment established — a label-only track yields to a described
fallback (the memory outline's establishes lines), never to a display dump.
Key ideas are the argument: extraction stays complete and assembly caps them at
2 × §9's key-ideas count for the length (S01 63 min → 24), rank-sampled in source
order. The numbers strip takes only key ideas the model labelled
``kind: number`` with the ``figure`` it gave — nothing is mined from their text.
Food and fitness numbers are null unless stated. Every goal a fallback can emit
names what the viewer gets. The measure-unit list, the procedure components,
the step-list / step-timeline domains and the attachment thresholds come from
domains.json; a workout ships whole; a dense answer tab whose summary repeats its
goal gets its first item; a D6-removed Moments tab comes back when the dedupe
leaves no step tab.
"""

from __future__ import annotations

import pytest

from src.models.domain_types import FitnessMeta
from src.services.pipeline.assembly import assemble_response
from src.services.pipeline.assembly.attachments import (
    _DENSE_THRESHOLD,
    SPARSE_THRESHOLD,
    attach_secondaries,
)
from src.services.pipeline.assembly.component_lists import SEQUENTIAL_COMPONENTS
from src.services.pipeline.assembly.core import _cap_tab_items, key_idea_caps
from src.services.pipeline.assembly.density import moment_gate_passes
from src.services.pipeline.assembly.labels import _MEASURE_UNITS, true_count_label
from src.services.pipeline.assembly.promotion import STEP_LIST_DOMAINS
from src.services.pipeline.assembly.sampling import (
    key_idea_cap,
    moment_cap_for_duration,
    moment_track_cap,
)
from src.services.pipeline.assembly.stat_banner import (
    _STAT_BANNER_MIN,
    _key_idea_tile,
    _stat_banner_attachment,
)
from src.services.pipeline.assembly.steps_timeline import (
    STEPS_ARE_THE_TIMELINE,
    restore_moments_without_steps,
)
from src.services.pipeline.extraction_prompt import load_schema_text
from src.services.pipeline.plan_prompt import goal_rule_violation
from src.services.pipeline.schema_fields import slice_skeleton
from src.shared_config.assembly_policy import assembly_policy, measure_units
from src.shared_config.domain_config import get_config

_LABELS = [{"time": f"{m}:00", "seconds": 60 * m, "label": f"Chapter {m}"} for m in range(1, 9)]
_OUTLINE = [
    {
        "start": 60 * i,
        "end": 60 * (i + 1),
        "title": f"Part {i}",
        "establishes": f"Part {i} establishes that the argument holds at step {i}.",
    }
    for i in range(0, 8)
]
# The four tiles the S01 iteration's numbers strip shipped, as their key ideas.
_S01_NUMBERS = [
    "November 24, 2025, the release of Opus 4.5, is the Kodak Brownie moment of software",
    "His own output proves the shift: in 20 months he has written half as much code as in "
    "the previous 21 years",
    "Where to cut the line between native and web is still unknown, but his own output "
    "changed by orders of magnitude: 150,000 lines in one month",
    "Omarchy's install time has collapsed from 3 minutes 33 seconds to 35 seconds in the field",
]


def _moments_tab() -> dict:
    return {
        "id": "moments",
        "label": "Moments",
        "component": "moment_track",
        "dataSource": "learning.timestamps",
        "goal": "g",
    }


# ─── Moments: sized by length, gated on substance ───


class TestMomentGate:
    def test_should_fail_the_gate_when_the_moments_are_labels(self) -> None:
        assert not moment_gate_passes({"items": _LABELS})

    def test_should_pass_the_gate_when_the_moments_say_what_they_established(self) -> None:
        items = [{**m, "description": f"What {m['label']} established."} for m in _LABELS]

        assert moment_gate_passes({"items": items})

    def test_should_ship_the_described_outline_when_the_planned_moments_are_labels(self) -> None:
        triage = {"primaryTag": "learning", "contentTags": ["learning"], "tabs": [_moments_tab()]}
        meta = {"duration": 600, "outline": _OUTLINE, "chapters": []}

        result = assemble_response(
            triage, {"learning": {"timestamps": _LABELS}}, None, None, video_meta=meta
        )
        items = next(t for t in result["tabs"] if t["id"] == "moments")["props"]["items"]

        assert items and all(i.get("description") for i in items)

    def test_should_keep_the_labels_when_no_described_fallback_exists(self) -> None:
        triage = {"primaryTag": "learning", "contentTags": ["learning"], "tabs": [_moments_tab()]}

        result = assemble_response(
            triage, {"learning": {"timestamps": _LABELS}}, None, None, video_meta={"duration": 600}
        )
        tab = next(t for t in result["tabs"] if t["id"] == "moments")

        assert tab["component"] == "moment_track" and len(tab["props"]["items"]) > 0

    @pytest.mark.parametrize(("minutes", "cap"), [(10, 4), (30, 8), (63, 12), (120, 20)])
    def test_should_size_a_label_only_track_by_the_registry_bands(
        self, minutes: int, cap: int
    ) -> None:
        assert moment_cap_for_duration(minutes * 60, 20) == cap


# ─── Key ideas: 2 × §9, never sampled to a screen ───


class TestKeyIdeaCap:
    def _spots(self, n: int) -> list[dict]:
        return [
            {"name": f"Idea {i}", "description": "d" * (i % 3), "timestamp": 60 * i}
            for i in range(n)
        ]

    @pytest.mark.parametrize(("minutes", "cap"), [(10, 10), (30, 16), (63, 24), (120, 32)])
    def test_should_cap_key_ideas_at_twice_the_length_count(self, minutes: int, cap: int) -> None:
        assert key_idea_cap(minutes * 60, 25) == cap

    def test_should_ship_24_of_37_key_ideas_on_a_63_minute_talk_in_source_order(self) -> None:
        props = {"spots": self._spots(37)}
        caps = key_idea_caps(
            {"key_ideas": "learning.keyIdeas", "other": "learning.keyPoints"}, 3780
        )

        _cap_tab_items("spot_explorer", props, 3780, None, caps["key_ideas"])
        stamps = [s["timestamp"] for s in props["spots"]]

        assert (len(stamps), stamps == sorted(stamps), "other" in caps) == (24, True, False)

    def test_should_leave_another_spot_tab_to_the_component_cap(self) -> None:
        props = {"spots": self._spots(37)}

        _cap_tab_items("spot_explorer", props, 3780, None, None)

        assert len(props["spots"]) == 25


class TestMomentsStrictlyBySection9:
    def _described(self, n: int) -> list[dict]:
        return [
            {"seconds": 30 * i, "label": f"L{i}", "description": f"Beat {i} established a point."}
            for i in range(n)
        ]

    def test_should_keep_a_described_track_to_its_band_max(self) -> None:
        assert moment_track_cap(self._described(14), 600, 20) == 6

    def test_should_give_a_long_described_talk_its_band_max(self) -> None:
        assert moment_track_cap(self._described(30), 3780, 20) == 18

    @pytest.mark.parametrize(("minutes", "cap"), [(100, 18), (280, 46), (600, 60)])
    def test_should_give_an_open_ended_band_one_moment_per_six_minutes(
        self, minutes: int, cap: int
    ) -> None:
        assert moment_track_cap(self._described(80), minutes * 60, 20) == cap


# ─── The numbers strip: the model's own figures only ───


def _number_idea(i: int, figure: dict | None, kind: str = "number") -> dict:
    return {
        "claim": f"Claim {i} turns on a figure",
        "kind": kind,
        "timestamp": 60 * (i + 1),
        "figure": figure,
    }


class TestNumbersStrip:
    @pytest.mark.parametrize("claim", _S01_NUMBERS)
    def test_should_mine_no_tile_from_a_number_ideas_text(self, claim: str) -> None:
        assert _key_idea_tile({"claim": claim, "kind": "number", "timestamp": 700}) is None

    def test_should_tile_the_figure_the_model_gave(self) -> None:
        tile = _key_idea_tile(_number_idea(0, {"value": "99 %", "label": "less backend CPU"}))

        assert tile == {
            "label": "less backend CPU",
            "value": "99 %",
            "meaning": "Claim 0 turns on a figure",
            "timestamp": 60,
        }

    def test_should_skip_an_idea_that_is_no_number(self) -> None:
        idea = _number_idea(0, {"value": "99 %", "label": "less CPU"}, kind="argument")

        assert _key_idea_tile(idea) is None

    def test_should_ship_no_strip_under_five_model_figures(self) -> None:
        ideas = [_number_idea(i, {"value": f"{i}0 %", "label": "share"}) for i in range(4)]

        assert _stat_banner_attachment({"learning": {"keyIdeas": ideas}}, "learning") is None

    def test_should_ship_the_strip_with_five_model_figures(self) -> None:
        ideas = [
            _number_idea(i, {"value": f"{i}0 %", "label": "share"}) for i in range(_STAT_BANNER_MIN)
        ]

        banner = _stat_banner_attachment({"learning": {"keyIdeas": ideas}}, "learning")

        assert banner is not None and len(banner["props"]["stats"]) == _STAT_BANNER_MIN


# ─── Numbers nobody said (D13, S06) ───


class TestStatedNumbersOnly:
    def test_should_leave_an_unstated_workout_length_empty(self) -> None:
        assert FitnessMeta.model_validate({"duration": None}).duration is None

    @pytest.mark.parametrize("domain", ["food", "fitness"])
    def test_should_ask_for_a_number_only_as_stated(self, domain: str) -> None:
        assert "only" in load_schema_text(domain) and "else null" in load_schema_text(domain)

    def test_should_ask_for_no_rest_time_the_instructor_did_not_say(self) -> None:
        exercise = slice_skeleton("fitness", ["exercises"])["exercises"][0]

        assert exercise["sets"] is None and exercise["rest"].startswith("Only if the instructor")


# ─── Every goal a fallback can emit names the viewer's outcome ───


def _fallback_goals() -> list[tuple[str, str]]:
    cfg = get_config()
    goals = [
        (f"{d}.{t['id']}", t["goal"]) for d, c in cfg["domains"].items() for t in c["defaultTabs"]
    ]
    return goals + [(f"injected.{k}", v["goal"]) for k, v in cfg["injectedTabs"].items()]


class TestFallbackGoals:
    @pytest.mark.parametrize(
        ("where", "goal"), _fallback_goals(), ids=[w for w, _ in _fallback_goals()]
    )
    def test_should_name_what_the_viewer_gets_in_every_fallback_goal(
        self, where: str, goal: str
    ) -> None:
        assert goal_rule_violation(goal) is None

    def test_should_ship_assembly_added_tabs_with_registry_goals(self) -> None:
        triage = {"primaryTag": "learning", "contentTags": ["learning"], "tabs": []}
        meta = {"duration": 600, "outline": _OUTLINE, "chapters": []}

        result = assemble_response(
            triage,
            {"learning": {}},
            None,
            {"tldr": "t", "keyTakeaways": ["a", "b"]},
            video_meta=meta,
        )

        assert result["tabs"] and all(
            goal_rule_violation(t["goal"]) is None for t in result["tabs"]
        )


# ─── Single-sourced sets (D8) ───


class TestRegistrySets:
    def test_should_read_the_measure_units_from_the_registry(self) -> None:
        assert _MEASURE_UNITS == measure_units()

    def test_should_leave_a_measure_label_verbatim(self) -> None:
        assert true_count_label("7 Days in Tokyo", 3) == "7 Days in Tokyo"

    def test_should_ship_a_workout_whole_as_a_procedure(self) -> None:
        assert {"step_player", "step_flow_canvas", "workout_room"} == SEQUENTIAL_COMPONENTS

    def test_should_read_the_step_list_domains_from_the_registry(self) -> None:
        assert set(assembly_policy()["stepListDomains"]) == STEP_LIST_DOMAINS

    def test_should_read_the_attachment_thresholds_from_the_registry(self) -> None:
        policy = assembly_policy()

        assert (SPARSE_THRESHOLD, _DENSE_THRESHOLD) == (
            policy["sparseItemsMax"],
            policy["denseItemsMin"],
        )


# ─── Headers and D6 ───


class TestAnswerHeaderAndD6:
    def test_should_head_a_dense_answer_tab_with_its_first_item_when_the_summary_is_its_goal(
        self,
    ) -> None:
        spots = [{"name": f"Spot {i}", "description": f"detail {i}"} for i in range(12)]
        tab = {
            "id": "a",
            "component": "spot_explorer",
            "goal": "Know the ideas.",
            "props": {"spots": spots},
        }
        extraction = {"learning": {"summary": "Know the ideas."}}

        out = attach_secondaries(tab, extraction, None, None, "learning", is_answer_tab=True)

        assert out[0]["props"]["summary"] == "Spot 0 — detail 0"

    def test_should_restore_the_moments_when_no_step_tab_survives(self) -> None:
        moments = {"id": "moments", "component": "moment_track", "props": {"items": _LABELS}}
        tabs = [{"id": "overview", "component": "overview"}]
        dropped = [{"id": "moments", "reason": STEPS_ARE_THE_TIMELINE}]

        restored = restore_moments_without_steps(tabs, dropped, [(1, moments)], "food", [])

        assert (restored, [t["id"] for t in tabs], dropped) == (True, ["overview", "moments"], [])

    def test_should_keep_the_moments_out_when_a_step_tab_survives(self) -> None:
        moments = {"id": "moments", "component": "moment_track", "props": {"items": _LABELS}}
        tabs = [{"id": "steps", "component": "step_player"}]

        assert not restore_moments_without_steps(tabs, [], [(1, moments)], "food", [])
