"""Drift canary: golden per-domain LLM response fixtures through the parsers + assembly.

Fixtures under ``tests/fixtures/llm_responses/`` are hand-built, VALID
per-stage LLM outputs (plan / extraction / enrichment / synthesis) for a
diverse set of domains. These tests replay them through the same code that
handles real LLM responses:

- strict Pydantic validation (``DOMAIN_MODELS``, ``PlanResult``,
  ``EnrichmentData``, ``SynthesisResult``)
- plan tab validation (``_validate_tabs``)
- the full assembly orchestrator (``assemble_response``)

A schema or assembler change that breaks parsing of previously-valid LLM
output fails here — before it silently breaks cached videos in production.
No LLM calls are made.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from src.models.domain_types import DOMAIN_MODELS, validate_domain_output
from src.models.pipeline_types import EnrichmentData, PlanResult, SynthesisResult
from src.services.pipeline.assembly import assemble_response
from src.services.pipeline.assembly.component_lists import item_list_keys
from src.services.pipeline.plan import _validate_tabs
from src.services.pipeline.reconcile_pass import is_quiz_tab
from src.shared_config.domain_config import quiz_enrichment

FIXTURES_DIR = Path(__file__).parent / "fixtures" / "llm_responses"

DOMAINS = ["tech", "travel", "review", "food", "science", "news"]

# Signature components that must survive assembly per domain. Sets include
# promotion targets (e.g. comparison → comparison_radar) — a promoted tab
# still satisfies the domain.
_EXPECTED_COMPONENTS: dict[str, list[set[str]]] = {
    "tech": [{"code_playground"}],
    "travel": [{"spot_explorer"}],
    "review": [{"comparison", "comparison_radar"}],
    "food": [{"step_player", "step_flow_canvas"}, {"checklist"}],
    "science": [{"concept_canvas", "connect_canvas"}],
    "news": [{"claims_tracker"}],
}


def _load(domain: str) -> dict[str, Any]:
    return json.loads((FIXTURES_DIR / f"{domain}.json").read_text())


def _quiz_domains(*, with_enrichment: bool) -> list[str]:
    """The fixtures' quiz domains whose enrichment is given (or null)."""
    return [
        d
        for d in DOMAINS
        if d in quiz_enrichment()["quizDomains"]
        and (_load(d)["enrichment_response"] is not None) == with_enrichment
    ]


def _assemble(fixture: dict[str, Any]) -> dict[str, Any]:
    """Replay the fixture through validation + assembly like the pipeline does."""
    domain = fixture["domain"]
    plan = PlanResult.model_validate(fixture["plan_response"])
    plan.tabs = _validate_tabs(plan.tabs)
    extraction = validate_domain_output([domain], plan.modifiers, fixture["extraction_response"])
    enrichment_raw = fixture["enrichment_response"]
    enrichment = (
        EnrichmentData.model_validate(enrichment_raw).model_dump(by_alias=True)
        if enrichment_raw is not None
        else None
    )
    synthesis = SynthesisResult.model_validate(
        fixture["synthesis_response"],
    ).model_dump(by_alias=True)
    return assemble_response(
        triage=plan.to_triage_dict(),
        extraction=extraction,
        enrichment=enrichment,
        synthesis=synthesis,
        video_meta=fixture["video_meta"],
    )


# ─── Stage payload validation (schema drift canary) ─────────────────────
class TestFixturePayloadsStayValid:
    @pytest.mark.parametrize("domain", DOMAINS)
    def test_extraction_response_validates_strictly(self, domain):
        """Raises (fails) when a domain model change rejects this payload."""
        fixture = _load(domain)
        model_cls = DOMAIN_MODELS[domain]
        instance = model_cls.model_validate(fixture["extraction_response"])
        dumped = instance.model_dump(by_alias=True)
        assert any(v for v in dumped.values() if v not in (None, [], {}, ""))

    @pytest.mark.parametrize("domain", DOMAINS)
    def test_extraction_round_trip_preserves_content_fields(self, domain):
        """Undeclared Pydantic fields silently drop on model_dump — catch that.

        Every top-level list/str field present in the fixture payload must
        survive the validate → dump round trip (aliases considered).
        """
        fixture = _load(domain)
        model_cls = DOMAIN_MODELS[domain]
        dumped = model_cls.model_validate(fixture["extraction_response"]).model_dump(by_alias=True)
        for key, value in fixture["extraction_response"].items():
            if isinstance(value, list) and value:
                assert dumped.get(key), f"{domain}.{key} dropped in round trip"
            elif isinstance(value, str) and value:
                assert dumped.get(key), f"{domain}.{key} dropped in round trip"

    @pytest.mark.parametrize("domain", DOMAINS)
    def test_plan_response_validates_and_tabs_survive(self, domain):
        fixture = _load(domain)
        plan = PlanResult.model_validate(fixture["plan_response"])
        assert plan.primary_tag == domain
        validated_tabs = _validate_tabs(plan.tabs)
        assert len(validated_tabs) == len(fixture["plan_response"]["tabs"]), (
            f"plan tab(s) rejected for {domain}: "
            f"{[t['id'] for t in fixture['plan_response']['tabs']]} → "
            f"{[t['id'] for t in validated_tabs]}"
        )

    @pytest.mark.parametrize("domain", DOMAINS)
    def test_synthesis_response_validates(self, domain):
        fixture = _load(domain)
        synthesis = SynthesisResult.model_validate(fixture["synthesis_response"])
        assert synthesis.tldr
        assert synthesis.key_takeaways

    # Only quiz domains get a quiz call (1d.1), and only when the plan uses one
    # (news joined the quiz domains in output-quality 1.5; its fixture run
    # planned no quiz, so it carries null like the others).
    @pytest.mark.parametrize("domain", _quiz_domains(with_enrichment=True))
    def test_enrichment_response_validates(self, domain):
        fixture = _load(domain)
        enrichment = EnrichmentData.model_validate(fixture["enrichment_response"])
        dumped = enrichment.model_dump(by_alias=True)
        assert any(v for v in dumped.values())

    @pytest.mark.parametrize("domain", _quiz_domains(with_enrichment=False))
    def test_should_plan_no_quiz_tab_when_a_quiz_domain_fixture_has_no_enrichment(self, domain):
        """A null enrichment is only honest for a plan that asked for no quiz — a
        planned quiz tab with nothing to show would be a skipped check (p1 R10)."""
        tabs = _load(domain)["plan_response"]["tabs"]

        assert not any(is_quiz_tab(tab) for tab in tabs)


# ─── Assembly drift canary ──────────────────────────────────────────────
class TestAssemblyOverFixtures:
    @pytest.mark.parametrize("domain", DOMAINS)
    def test_assembles_without_error_and_produces_tabs(self, domain):
        assembled = _assemble(_load(domain))
        assert isinstance(assembled, dict)
        assert len(assembled["tabs"]) >= 3

    @pytest.mark.parametrize("domain", DOMAINS)
    def test_overview_tab_is_first(self, domain):
        assembled = _assemble(_load(domain))
        assert assembled["tabs"][0]["component"] == "overview"

    @pytest.mark.parametrize("domain", DOMAINS)
    def test_signature_components_survive(self, domain):
        assembled = _assemble(_load(domain))
        components = {t["component"] for t in assembled["tabs"]}
        for accepted in _EXPECTED_COMPONENTS[domain]:
            assert components & accepted, (
                f"{domain}: none of {sorted(accepted)} survived assembly (got {sorted(components)})"
            )

    @pytest.mark.parametrize("domain", DOMAINS)
    def test_every_tab_is_structurally_sound(self, domain):
        assembled = _assemble(_load(domain))
        for tab in assembled["tabs"]:
            assert tab["id"], f"tab without id in {domain}"
            assert tab["label"], f"tab {tab['id']} has empty label"
            assert isinstance(tab["props"], dict) and tab["props"], (
                f"tab {tab['id']} has empty props"
            )
            # Any of the component's item lists (a comparison ships on rows, pros or
            # cons — the review's Pros & cons tab has no rows, 4.3).
            keys = item_list_keys(tab["component"])
            if keys:
                assert any(
                    isinstance(tab["props"].get(k), list) and tab["props"][k] for k in keys
                ), f"{domain}/{tab['id']}: every required list {keys!r} empty"

    @pytest.mark.parametrize("domain", DOMAINS)
    def test_meta_carries_domain_and_synthesis(self, domain):
        assembled = _assemble(_load(domain))
        meta = assembled["meta"]
        assert meta["primaryTag"] == domain
        assert meta["tldr"]
        assert meta["keyTakeaways"]

    @pytest.mark.parametrize("domain", DOMAINS)
    def test_no_planned_tab_is_silently_lost(self, domain):
        """Planned tabs must assemble (or be represented by a promoted id).

        This is the sharp edge of the canary: a normalizer change that starts
        rejecting valid extraction data shows up as a dropped tab here.
        """
        fixture = _load(domain)
        assembled = _assemble(fixture)
        assembled_ids = {t["id"] for t in assembled["tabs"]}
        planned_ids = {t["id"] for t in fixture["plan_response"]["tabs"]}
        missing = planned_ids - assembled_ids
        assert not missing, f"{domain}: planned tabs dropped by assembly: {sorted(missing)}"
