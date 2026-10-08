"""Tests for the Plan stage tab validator and domain policy.

`_validate_tabs` normalizes each LLM tab — id, component, dataSource — and
carries its ``brief`` (pipeline-1min 1b.2: what to pull, where, how many) into
the shape extraction and assembly read.
"""

from __future__ import annotations

from src.services.pipeline.plan import _validate_tabs
from src.shared_config.domain_config import valid_components


def _tab(**overrides: object) -> dict:
    return {"id": "steps", "label": "Steps", "component": "step_player", **overrides}


class TestValidateTabsBrief:
    """`_validate_tabs` keeps the planner's brief, normalized."""

    def test_should_keep_brief_when_planner_sends_one(self):
        brief = {"what": "every step with time", "where": ["1:10-2:40"], "expect": 8}

        result = _validate_tabs([_tab(brief=brief)])

        assert result[0]["brief"] == brief

    def test_should_give_empty_brief_when_planner_omits_it(self):
        result = _validate_tabs([_tab()])

        assert result[0]["brief"] == {"what": "", "where": [], "expect": 0}

    def test_should_drop_malformed_ranges_when_normalizing_where(self):
        brief = {"where": ["1:10 – 2:40", "the middle part", 42, "14:00"]}

        result = _validate_tabs([_tab(brief=brief)])

        assert result[0]["brief"]["where"] == ["1:10-2:40", "14:00"]

    def test_should_read_a_count_when_expect_is_text(self):
        result = _validate_tabs([_tab(brief={"expect": "~14 items"})])

        assert result[0]["brief"]["expect"] == 14

    def test_should_give_empty_brief_when_brief_is_not_an_object(self):
        result = _validate_tabs([_tab(brief="all the steps")])

        assert result[0]["brief"] == {"what": "", "where": [], "expect": 0}

    def test_should_not_carry_outbound_links_when_an_old_prompt_sends_them(self):
        result = _validate_tabs([_tab(outboundLinks={"quiz": "Test yourself"})])

        assert "outboundLinks" not in result[0]


class TestVerdictNeverStandalone:
    """`verdict` is a retired component name (1C). It must never reach the
    frontend as a standalone component: the verdict score / bottom-line folds
    into the comparison ReviewSummary header instead. Locking this in code so a
    future edit to the component list can't silently reintroduce the audited
    'verdict renders as raw DisplaySection' regression."""

    def test_verdict_is_not_a_valid_component(self):
        assert "verdict" not in valid_components()

    def test_explicit_verdict_component_coerced_to_comparison(self):
        tabs = [
            {
                "id": "verdict",
                "label": "Verdict",
                "component": "verdict",
                "dataSource": "review.verdict",
            }
        ]
        result = _validate_tabs(tabs)
        assert result[0]["component"] == "comparison"

    def test_verdict_tab_id_infers_comparison(self):
        tabs = [{"id": "verdict", "label": "Verdict", "dataSource": "review.verdict"}]
        result = _validate_tabs(tabs)
        assert result[0]["component"] == "comparison"


class TestEnforceDomainPolicy:
    """Forbidden components are stripped at plan time — before extraction."""

    def test_quiz_removed_for_gaming(self):
        from src.services.pipeline.plan import _enforce_domain_policy

        tabs = [
            {"id": "pulls", "component": "tier_list"},
            {"id": "quiz", "component": "quiz_arena"},
        ]
        kept = _enforce_domain_policy(tabs, "gaming", None)

        assert [t["id"] for t in kept] == ["pulls"]

    def test_playbook_forbidden_union_applies(self):
        from src.services.pipeline.plan import _enforce_domain_policy

        tabs = [
            {"id": "pulls", "component": "tier_list"},
            {"id": "code", "component": "code_playground"},
        ]
        kept = _enforce_domain_policy(tabs, "gaming", "unboxing")

        # code_playground is forbidden by the gaming:unboxing playbook only
        assert [t["id"] for t in kept] == ["pulls"]

    def test_educational_domain_keeps_quiz(self):
        from src.services.pipeline.plan import _enforce_domain_policy

        tabs = [{"id": "quiz", "component": "quiz_arena"}]
        kept = _enforce_domain_policy(tabs, "learning", None)

        assert kept == tabs

    def test_component_inferred_from_id_when_missing(self):
        from src.services.pipeline.plan import _enforce_domain_policy

        tabs = [{"id": "quiz"}]  # infer_component("quiz") -> quiz_arena family
        kept = _enforce_domain_policy(tabs, "gaming", None)

        assert kept == [] or kept[0].get("component") not in ("quiz_arena",)


class TestRenderPlaybook:
    def test_gaming_unboxing_renders_full_block(self):
        from src.services.pipeline.plan_prompt import _render_playbook

        block = _render_playbook("gaming", "unboxing")

        assert '<playbook for="gaming:unboxing">' in block
        assert "tier_list" in block
        assert "quiz_arena" in block  # listed as forbidden
        assert "Never quiz the viewer" in block

    def test_should_state_required_component_as_conditional_when_rendering(self):
        from src.services.pipeline.plan_prompt import _render_playbook

        block = _render_playbook("gaming", "unboxing")

        assert "Required components: `tier_list` only when has_ranking is true" in block

    def test_no_playbook_renders_empty(self):
        from src.services.pipeline.plan_prompt import _render_playbook

        assert _render_playbook("cooking", "tutorial") == ""
        assert _render_playbook(None, "unboxing") == ""
        assert _render_playbook("gaming", None) == ""
