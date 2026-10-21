"""p1.4 — claims unfenced (D11, L10) and the review verdict's timestamp (R2, D13).

``news.claims`` is generalised: learning, podcast and tech carry the same claim
shape on ``has_claims``, so a talk with checkable claims may plan claims_tracker.
A claim's status says what the video does with it — stated / supported /
contested ONLY; any other word fails validation (the status goes, the claim
stays) and assembly never ships "verified" (stored rows read in the new words).
The review verdict's badge, the review score and a gaming tier render only
with the timestamp the extraction now carries for them. The requirement
backfill never borrows a source another tab ships (R5).
"""

from __future__ import annotations

import pytest

from src.models.domain_types import (
    GamingRanking,
    ReviewData,
    validate_domain_output_with_drops,
)
from src.models.idea_types import CLAIM_STATUSES, Claim
from src.models.memory_types import MemoryResult
from src.models.pipeline_types import PlanResult
from src.services.pipeline.assembly import assemble_response
from src.services.pipeline.assembly.assemblers_secondary import (
    assemble_claims_tracker,
    assemble_tier_list,
)
from src.services.pipeline.assembly.core import _validate_domain_requirements
from src.services.pipeline.assembly.normalizers import _normalize_verdict
from src.services.pipeline.extraction_prompt import load_schema_text
from src.services.pipeline.plan_reconcile import reconcile_plan_tabs
from src.services.pipeline.reconcile import reconcile_at_plan_done
from src.services.pipeline.schema_fields import slice_skeleton
from src.shared_config.domain_config import data_source

_CLAIM_PATHS = ("learning.claims", "podcast.claims", "tech.claims", "news.claims")
_CLAIMS = [
    {
        "claim": "Agents wrote 150,000 lines in August",
        "status": "supported",
        "sourceCitation": "His commit log on screen",
        "timestamp": 660,
    },
    {
        "claim": "Hand-written code is no longer economically productive",
        "status": "stated",
        "timestamp": 1262,
    },
    {
        "claim": "AI will end programming jobs",
        "status": "contested",
        "source": "Critics",
        "timestamp": 3382,
    },
]


def _tab(tab_id: str, component: str, source: str) -> dict:
    return {"id": tab_id, "label": tab_id, "component": component, "dataSource": source}


# ─── Model: three words only ───


class TestClaimStatusVocabulary:
    def test_should_name_exactly_three_statuses(self) -> None:
        assert CLAIM_STATUSES == ("stated", "supported", "contested")

    @pytest.mark.parametrize(
        "status", ["verified", "disputed", "context", "true", "stated | supported | contested"]
    )
    def test_should_refuse_a_status_outside_the_three_words(self, status: str) -> None:
        assert Claim.model_validate({"claim": "c", "status": status}).status is None

    def test_should_accept_a_status_in_any_case(self) -> None:
        assert Claim.model_validate({"claim": "c", "status": " Supported "}).status == "supported"

    def test_should_keep_the_claim_and_drop_the_status_when_an_item_is_validated(self) -> None:
        raw = {"learning": {"claims": [{"claim": "c", "status": "verified", "timestamp": 5}]}}

        data, _ = validate_domain_output_with_drops(["learning"], [], raw)

        assert (
            data["learning"]["claims"][0]["claim"],
            data["learning"]["claims"][0]["status"],
        ) == (
            "c",
            None,
        )


# ─── Registry + schemas ───


class TestClaimsRegistry:
    @pytest.mark.parametrize("path", _CLAIM_PATHS)
    def test_should_gate_every_claims_path_on_has_claims(self, path: str) -> None:
        spec = data_source(path)

        assert spec is not None
        assert (spec["components"], spec["requiresEvidence"]) == (["claims_tracker"], "has_claims")

    @pytest.mark.parametrize("domain", ["learning", "podcast", "tech", "news"])
    def test_should_ask_every_domain_for_the_one_claim_shape(self, domain: str) -> None:
        item = slice_skeleton(domain, ["claims"])["claims"][0]

        assert set(item) == {"claim", "source", "status", "sourceCitation", "timestamp"}

    @pytest.mark.parametrize("domain", ["learning", "podcast", "tech", "news"])
    def test_should_offer_only_the_three_words_in_every_schema(self, domain: str) -> None:
        item = slice_skeleton(domain, ["claims"])["claims"][0]

        assert item["status"] == "stated | supported | contested"

    def test_should_never_tell_the_model_an_unattributed_claim_is_reporter(self) -> None:
        news = load_schema_text("news")

        assert '"source": "Reporter"' not in news and 'is "Reporter"' not in news


# ─── Planning: a talk may plan claims_tracker ───


class TestTalkPlansClaims:
    def _plan(self, evidence: dict) -> PlanResult:
        tabs = [
            _tab("ideas", "spot_explorer", "learning.keyIdeas"),
            _tab("claims", "claims_tracker", "learning.claims"),
        ]
        return PlanResult.model_validate(
            {
                "contentTags": ["learning"],
                "primaryTag": "learning",
                "confidence": 0.9,
                "evidence": evidence,
                "tabs": tabs,
            }
        )

    def test_should_keep_a_talks_claims_tab_when_it_has_claims(self) -> None:
        outcome = reconcile_at_plan_done(self._plan({"has_claims": True}), None, "talk")

        assert "claims" in [t["id"] for t in outcome.tabs]

    def test_should_plan_no_claims_tracker_when_both_readers_deny_claims(self) -> None:
        memory = MemoryResult(evidence={"has_claims": False})

        outcome = reconcile_at_plan_done(self._plan({"has_claims": False}), memory, "talk")

        assert "claims_tracker" not in [t["component"] for t in outcome.tabs]

    def test_should_repoint_news_claims_to_learning_claims_on_a_learning_video(self) -> None:
        tabs = [
            _tab("ideas", "spot_explorer", "learning.keyIdeas"),
            _tab("cases", "claims_tracker", "news.claims"),
        ]

        kept, _, _ = reconcile_plan_tabs(tabs, ["learning", "tech"], [])

        assert next(t["dataSource"] for t in kept if t["id"] == "cases") == "learning.claims"


# ─── Assembly: never "verified" ───


class TestClaimsAssembly:
    def test_should_ship_a_talks_claims_tab_with_the_three_words(self) -> None:
        triage = {
            "primaryTag": "learning",
            "contentTags": ["learning"],
            "tabs": [_tab("claims", "claims_tracker", "learning.claims")],
        }

        result = assemble_response(triage, {"learning": {"claims": _CLAIMS}}, None, None)
        claims = next(t for t in result["tabs"] if t["id"] == "claims")["props"]["claims"]

        assert [c["status"] for c in claims] == ["supported", "stated", "contested"]

    def test_should_read_the_stored_words_in_the_new_vocabulary(self) -> None:
        stored = [
            {"claim": "a", "status": "verified", "timestamp": 1},
            {"claim": "b", "status": "disputed", "timestamp": 2},
            {"claim": "c", "status": "context", "timestamp": 3},
        ]

        props = assemble_claims_tracker({}, stored, {}, None)

        assert props is not None
        assert [c["status"] for c in props["claims"]] == ["supported", "contested", "stated"]

    def test_should_state_a_status_the_video_cannot_be_checked_at(self) -> None:
        unanchored = [{"claim": "a", "status": "supported"}, {"claim": "b", "status": "contested"}]

        props = assemble_claims_tracker({}, unanchored, {}, None)

        assert props is not None and {c["status"] for c in props["claims"]} == {"stated"}

    def test_should_omit_the_source_when_the_video_names_no_one(self) -> None:
        props = assemble_claims_tracker({}, _CLAIMS, {}, None)

        assert props is not None and "source" not in props["claims"][0]


# ─── Verdict, score, tier: only with a timestamp ───


class TestAnchoredReviewAndRanks:
    _VERDICT = {"badge": "recommended", "bottomLine": "Buy it", "bestFor": [], "notFor": []}

    def test_should_omit_the_badge_when_the_verdict_has_no_timestamp(self) -> None:
        assert "badge" not in _normalize_verdict(self._VERDICT, {})

    def test_should_ship_the_badge_and_its_seek_point_when_the_verdict_is_stamped(self) -> None:
        props = _normalize_verdict({**self._VERDICT, "timestamp": 598}, {})

        assert (props["badge"], props["timestamp"]) == ("recommended", 598)

    def test_should_keep_the_verdict_and_rating_stamps_through_validation(self) -> None:
        review = ReviewData.model_validate(
            {
                "rating": {"score": 8.5, "maxScore": 10, "timestamp": "612"},
                "verdict": {"badge": "recommended", "timestamp": 598},
            }
        ).model_dump(by_alias=True)

        assert (review["rating"]["timestamp"], review["verdict"]["timestamp"]) == (612, 598)

    def test_should_render_a_stated_score_when_the_rating_is_stamped(self) -> None:
        props = _normalize_verdict(self._VERDICT, {"score": 8.5, "maxScore": 10, "timestamp": 612})

        assert (props["score"], props["maxScore"]) == (8.5, 10)

    def test_should_render_tiers_when_validated_rankings_carry_their_stamps(self) -> None:
        rows = [
            GamingRanking.model_validate({"item": n, "tier": t, "timestamp": s}).model_dump()
            for n, t, s in (("Jett", "S", 130), ("Sage", "A", 205), ("Yoru", "C", 340))
        ]

        props = assemble_tier_list({}, rows, {}, None)

        assert props is not None and [i["tier"] for i in props["items"]] == ["S", "A", "C"]


# ─── Requirement backfill (g0b phase-1 note) ───


class TestBackfillRefusesClaimedSources:
    """A news video whose own claims came back empty while a learning claims tab
    ships the talk's claims: the required claims_tracker backfill must not borrow
    them (the dedupe would then drop the planned tab as its twin)."""

    def test_should_not_borrow_a_source_another_tab_ships_when_backfilling(self) -> None:
        tabs: list[dict] = []

        _validate_domain_requirements(
            tabs, "news", {"news": {}, "learning": {"claims": _CLAIMS}}, claimed={"learning.claims"}
        )

        assert tabs == []

    def test_should_backfill_from_the_other_domain_when_no_tab_ships_it(self) -> None:
        tabs: list[dict] = []

        _validate_domain_requirements(tabs, "news", {"news": {}, "learning": {"claims": _CLAIMS}})

        assert [t["component"] for t in tabs] == ["claims_tracker"]
