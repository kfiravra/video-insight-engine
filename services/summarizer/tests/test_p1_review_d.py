"""Phase-1 review fixes, lane D (registry): ``gates/review-p1-findings.md``.

- Key-ideas extraction cap (docs (a), Kfir 2026-10-21 decision 1): the registry cap of
  ``learning.keyIdeas`` is the 2× §9 ceiling of the longest band, so the plan's expect
  and the group job's ``(cap N)`` never stop extraction below the assembler's cap.
- R11-W5: every ``<component> on <path>`` pair a playbook's guidance names is a pair
  the registry lets the planner build (``review.specs`` renders as spot_explorer).
- R1-W4: a podcast whose probe format falls back to ``commentary`` still reaches the
  podcast playbook.
- docs (e): ``infoGridHostsNote`` says where the fold happens (plan-done,
  ``reconcile_pass.fold_step``).
- Types (R6-W4, R13-W3, R9-W2, lane B's FoodIngredient.amount): ``vie-response.ts``
  declares every field a Pydantic mirror emits, and takes a missing value wherever the
  mirror may emit None (quiz timestamp, fitness duration, ingredient amount, claim status).
"""

from __future__ import annotations

import re

import pytest
from pydantic import BaseModel
from pydantic.fields import FieldInfo

from src.models import domain_types as dt
from src.models import pipeline_types as pt
from src.models import vie_response_v2 as v2
from src.models.probe_types import TierProbe
from src.services.pipeline.assembly.sampling import key_idea_cap
from src.services.pipeline.field_requests import FieldRequest, requested_fields
from src.services.pipeline.schema_fields import scalar_fields
from src.shared_config.assembly_policy import assembly_policy
from src.shared_config.domain_config import (
    data_source,
    get_config,
    get_playbook,
    valid_components,
)
from tests.test_ts_pydantic_parity import _ALL, _IDS, Mapping, flat_interface_bodies, ts_fields

_KEY_IDEAS = "learning.keyIdeas"
# "spot_explorer on review.specs" — a component the guidance plans on a registry path.
_PLANNED_PAIR = re.compile(r"\b([a-z_]+) on ([a-z]+(?:\.[A-Za-z]+)+)")


def _key_ideas_ceiling() -> int:
    policy = assembly_policy()
    longest = max(band["count"] for band in policy["keyIdeasByDuration"])
    return longest * policy["keyIdeasCapFactor"]


def _key_ideas_request(expect: int) -> FieldRequest:
    tab = {
        "id": "ideas",
        "label": "Key ideas",
        "component": "spot_explorer",
        "dataSource": _KEY_IDEAS,
        "brief": {"what": "", "where": [], "expect": expect},
    }
    fields = requested_fields([tab], ["learning"], [], scalar_fields, primary_tag="learning")
    return next(f for f in fields if f.path == _KEY_IDEAS)


def _guidance_pairs() -> list[tuple[str, str, str]]:
    """(playbook, component, path) for every component the guidance plans on a path."""
    pairs: list[tuple[str, str, str]] = []
    for key, playbook in get_config()["playbooks"].items():
        for component, path in _PLANNED_PAIR.findall(playbook.get("planGuidance", "")):
            if component in valid_components():
                pairs.append((key, component, path))
    return pairs


class TestKeyIdeasExtractionCap:
    def test_should_ask_for_the_two_times_ceiling_of_the_longest_band(self) -> None:
        spec = data_source(_KEY_IDEAS)

        assert spec is not None and spec["cap"] == _key_ideas_ceiling()

    @pytest.mark.parametrize("minutes", [10, 30, 63, 120, 300])
    def test_should_never_ask_for_fewer_ideas_than_the_assembler_keeps(self, minutes: int) -> None:
        spec = data_source(_KEY_IDEAS)

        assert spec is not None and spec["cap"] >= key_idea_cap(minutes * 60, 25)

    def test_should_keep_the_plans_expect_when_a_63_minute_talk_argues_24_claims(self) -> None:
        assert _key_ideas_request(expect=24).expect == 24

    def test_should_print_the_ceiling_in_the_group_job_when_extraction_asks(self) -> None:
        assert _key_ideas_request(expect=24).cap == _key_ideas_ceiling()


class TestPlaybookGuidancePairs:
    def test_should_find_the_planned_pairs_in_the_guidance(self) -> None:
        assert ("review:review", "spot_explorer", "review.specs") in _guidance_pairs()

    @pytest.mark.parametrize(
        ("playbook", "component", "path"), _guidance_pairs(), ids=lambda v: str(v)
    )
    def test_should_register_the_component_on_the_path_when_the_guidance_plans_it(
        self, playbook: str, component: str, path: str
    ) -> None:
        spec = data_source(path)

        assert spec is not None and component in spec["components"], playbook

    def test_should_keep_the_review_specs_default_tab_component_first(self) -> None:
        spec = data_source("review.specs")

        assert spec is not None and spec["components"][0] == "info_grid"


class TestPodcastCommentaryAlias:
    def test_should_reach_the_podcast_playbook_when_the_probe_says_commentary(self) -> None:
        assert get_playbook("podcast", "commentary") == get_config()["playbooks"]["podcast:podcast"]

    def test_should_reach_the_podcast_playbook_when_the_probe_names_an_unknown_format(
        self,
    ) -> None:
        probe = TierProbe.model_validate(
            {"domain": "podcast", "format": "fireside", "has_visual_demo": False, "confidence": 0.8}
        )

        assert get_playbook(probe.domain, probe.format) == get_playbook("podcast", "podcast")

    def test_should_leave_a_learning_commentary_on_the_domain_defaults(self) -> None:
        # An explainer keeps keyPoints (D10): a commentary is not routed to the talk playbook.
        assert get_playbook("learning", "commentary") == {}


class TestInfoGridHostsNote:
    def test_should_name_the_plan_done_fold_step(self) -> None:
        note = get_config()["infoGridHostsNote"]

        assert "reconcile_pass.fold_step" in note and "plan-done" in note


# ─── TS ↔ Pydantic, the other direction (R6-W4, R13-W3, R9-W2, FoodIngredient.amount) ───

# A top-level TS member: name, optional marker, type text.
_MEMBER = re.compile(r"^\s*(?:readonly\s+)?([A-Za-z_]\w*)(\?)?\s*:\s*([^;\n]*)", re.MULTILINE)

# Mirror fields the TS contract deliberately leaves out.
_PYDANTIC_ONLY: dict[type[BaseModel], frozenset[str]] = {
    # Python-side fields of the assembled meta dict (vie_response_v2.VIEResponseMeta).
    v2.VIEResponseMeta: frozenset({"title", "duration"}),
}
# Mirror fields that may be None while TS types them required — the raw type never
# reaches the FE: the assembler normalizes narrative moments into MomentItems.
_NONE_TOLERATED: dict[type[BaseModel], frozenset[str]] = {
    dt.NarrativeKeyMoment: frozenset({"timestamp"}),
}


def _emitted(model: type[BaseModel]) -> dict[str, FieldInfo]:
    """The mirror's fields by the key they dump under (``by_alias``)."""
    return {info.alias or name: info for name, info in model.model_fields.items()}


def _own_members(interface: str) -> dict[str, bool]:
    """Own TS members → whether TS takes a missing value (optional or typed with null)."""
    _, flat = flat_interface_bodies()[interface]
    return {name: bool(optional) or "null" in typ for name, optional, typ in _MEMBER.findall(flat)}


def _none_refused(mapping: Mapping) -> set[str]:
    members = _own_members(mapping.ts_name)
    may_be_none = {
        key
        for key, info in _emitted(mapping.model).items()
        if not info.is_required() and info.default is None
    }
    refused = {key for key in may_be_none if key in members and not members[key]}
    return refused - _NONE_TOLERATED.get(mapping.model, frozenset())


class TestTsDeclaresWhatPydanticEmits:
    @pytest.mark.parametrize("mapping", _ALL, ids=_IDS)
    def test_should_declare_every_mirror_field_in_ts(self, mapping: Mapping) -> None:
        emitted = set(_emitted(mapping.model)) - _PYDANTIC_ONLY.get(mapping.model, frozenset())

        assert emitted - ts_fields(mapping.ts_name) == set()

    @pytest.mark.parametrize("mapping", _ALL, ids=_IDS)
    def test_should_take_a_missing_value_in_ts_when_the_mirror_defaults_to_none(
        self, mapping: Mapping
    ) -> None:
        assert _none_refused(mapping) == set()

    def test_should_cover_the_quiz_question_and_the_fitness_meta(self) -> None:
        covered = {(m.ts_name, m.model) for m in _ALL}

        assert {("QuizQuestion", pt.QuizQuestion), ("FitnessMeta", dt.FitnessMeta)} <= covered
