"""4.3 — a review opens on Pros & cons; the verdict rides in the hero only.

Kfir 2026-10-22 (Q4): the verdict lives in the hero, so the review playbook names
the answer tab "Pros & cons" and plans it on ``review.pros`` — the list the
comparison assembler ships pros and cons from (design §4.3) — and keeps the
verdict out of every tab. Best for / Not for stay in the review schema, filled
only when the reviewer states them (D13).
"""

from __future__ import annotations

from src.services.pipeline.extraction_prompt import load_schema_text
from src.services.pipeline.plan_prompt import _render_playbook
from src.shared_config.domain_config import get_config, get_playbook

_GUIDANCE = get_config()["playbooks"]["review:review"]["planGuidance"]


class TestReviewGuidance:
    def test_should_open_on_pros_and_cons_planned_on_review_pros(self) -> None:
        assert "open on Pros & cons — comparison on review.pros" in _GUIDANCE

    def test_should_keep_the_verdict_in_the_hero_when_the_review_is_planned(self) -> None:
        assert "the verdict rides in the hero, never in a tab" in _GUIDANCE

    def test_should_no_longer_name_a_verdict_tab_when_the_review_is_planned(self) -> None:
        assert "Verdict & pros/cons" not in _GUIDANCE

    def test_should_render_the_pros_and_cons_tab_when_the_probe_says_commentary(self) -> None:
        block = _render_playbook("review", "commentary")

        assert "Pros & cons" in block

    def test_should_reach_the_same_playbook_when_the_probe_says_comparison(self) -> None:
        assert get_playbook("review", "comparison")["planGuidance"] == _GUIDANCE


class TestBestForNotFor:
    def test_should_keep_best_for_and_not_for_in_the_review_schema(self) -> None:
        schema = load_schema_text("review")

        assert '"bestFor"' in schema and '"notFor"' in schema

    def test_should_fill_best_for_and_not_for_only_when_the_reviewer_states_them(self) -> None:
        assert "bestFor / notFor only when the reviewer states them" in load_schema_text("review")
