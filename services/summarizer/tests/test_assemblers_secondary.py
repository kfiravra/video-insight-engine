"""Assembler unit tests — secondary-tier + signature components.

diagram_card, claims_tracker, tier_list, formation_diagram.
"""

from src.models.domain_types import validate_domain_output
from src.services.pipeline.assembly import (
    assemble_claims_tracker,
    assemble_diagram_card,
    assemble_formation_diagram,
    assemble_tier_list,
)

# ─── resolve_data_source ───


# ─── DiagramCard (P3A) — nodes + edges from connections / step order ───


class TestAssembleDiagramCard:
    def test_builds_edges_from_connections(self):
        data = [
            {"label": "Client", "connections": ["Gateway"]},
            {"label": "Gateway", "connections": ["Service"]},
            {"label": "Service", "connections": []},
        ]
        result = assemble_diagram_card({}, data, {}, None)
        assert result is not None
        assert [n["label"] for n in result["nodes"]] == ["Client", "Gateway", "Service"]
        # Edges are index-addressed: Client(0)->Gateway(1), Gateway(1)->Service(2)
        assert {"source": 0, "target": 1} in result["edges"]
        assert {"source": 1, "target": 2} in result["edges"]

    def test_no_edges_key_without_connections(self):
        data = [{"label": "Step 1"}, {"label": "Step 2"}, {"label": "Step 3"}]
        result = assemble_diagram_card({}, data, {}, None)
        assert result is not None
        # No connections → frontend renders a sequential chain; no edges emitted.
        assert "edges" not in result

    def test_drops_self_and_out_of_range_edges(self):
        data = [
            {"label": "A", "connections": ["A", "Ghost"]},
            {"label": "B", "connections": ["A"]},
        ]
        result = assemble_diagram_card({}, data, {}, None)
        assert result is not None
        edges = result.get("edges", [])
        # self-edge (A->A) and ghost target dropped; only B(1)->A(0) survives
        assert {"source": 0, "target": 0} not in edges
        assert {"source": 1, "target": 0} in edges

    def test_caps_at_eight_nodes(self):
        data = [{"label": f"N{i}"} for i in range(12)]
        result = assemble_diagram_card({}, data, {}, None)
        assert result is not None
        assert len(result["nodes"]) == 8

    def test_returns_none_below_two_nodes(self):
        assert assemble_diagram_card({}, [{"label": "only"}], {}, None) is None


# ─── Phase 5b: claims_tracker (news signature component) ───


class TestAssembleClaimsTracker:
    def test_normalizes_full_claims(self):
        data = [
            {
                "claim": "Jobs created: 1200",
                "source": "Mayor",
                "status": "disputed",
                "sourceCitation": "Analysts say 700",
                "timestamp": 210,
            },
            {"claim": "Fares rise 15%", "source": "Authority", "status": "verified"},
        ]
        result = assemble_claims_tracker({}, data, {}, None)
        assert result is not None
        claims = result["claims"]
        assert len(claims) == 2
        assert claims[0]["status"] == "disputed"
        assert claims[0]["source"] == "Mayor"
        assert claims[0]["sourceCitation"] == "Analysts say 700"
        assert claims[0]["timestamp"] == 210

    def test_unknown_status_defaults_to_context(self):
        data = [
            {"claim": "A bold assertion", "source": "Pundit", "status": "true"},
            {"claim": "Another assertion", "source": "Pundit"},
        ]
        result = assemble_claims_tracker({}, data, {}, None)
        assert result["claims"][0]["status"] == "context"
        assert result["claims"][1]["status"] == "context"

    def test_should_leave_an_unattributed_claim_blank_never_reporter(self):
        data = [
            {"claim": "Claim one", "status": "context"},
            {"claim": "Claim two", "status": "verified"},
        ]
        result = assemble_claims_tracker({}, data, {}, None)
        assert result["claims"][0]["source"] == ""

    def test_should_leave_a_validated_unattributed_claim_blank(self):
        claims = [{"claim": "Claim one"}, {"claim": "Claim two", "source": "Mayor"}]
        news = validate_domain_output(["news"], [], {"claims": claims})["news"]

        result = assemble_claims_tracker({}, news["claims"], {}, None)

        assert [c["source"] for c in result["claims"]] == ["", "Mayor"]

    def test_dict_wrapper_with_claims_key(self):
        data = {
            "claims": [
                {"claim": "C1", "source": "S1", "status": "verified"},
                {"claim": "C2", "source": "S2", "status": "context"},
            ]
        }
        result = assemble_claims_tracker({}, data, {}, None)
        assert len(result["claims"]) == 2

    def test_drops_claims_with_no_text(self):
        data = [
            {"claim": "", "source": "S1", "status": "verified"},
            {"source": "S2", "status": "context"},
            {"claim": "Real claim", "source": "S3", "status": "verified"},
            {"claim": "Second real", "source": "S4", "status": "disputed"},
        ]
        result = assemble_claims_tracker({}, data, {}, None)
        assert len(result["claims"]) == 2

    def test_below_min_returns_none(self):
        # One real claim is below the 2-claim minimum.
        result = assemble_claims_tracker({}, [{"claim": "Only one", "source": "S"}], {}, None)
        assert result is None

    def test_empty_returns_none(self):
        assert assemble_claims_tracker({}, [], {}, None) is None
        assert assemble_claims_tracker({}, None, {}, None) is None


# ─── Phase 5c: tier_list (gaming signature component) ───


class TestAssembleTierList:
    # Rows carry a timestamp: a tier is a stated rank only when the row is
    # anchored in the video (0b.13, D13) — test_assembly_0b13_derived_guard.py
    # covers the unanchored case.
    def test_normalizes_full_rankings(self):
        data = [
            {"item": "Jett", "tier": "S", "reason": "Top duelist", "emoji": "🌪️", "timestamp": 10},
            {"item": "Sage", "tier": "a", "reason": "Carry", "timestamp": 20},
            {"item": "Yoru", "tier": "C", "timestamp": 30},
        ]
        result = assemble_tier_list({}, data, {}, None)
        assert result is not None
        items = result["items"]
        assert len(items) == 3
        assert items[0]["item"] == "Jett"
        assert items[0]["tier"] == "S"
        # lowercase tier coerced to uppercase
        assert items[1]["tier"] == "A"
        # Yoru had no reason — the key is omitted, not null
        assert "reason" not in items[2]

    def test_invalid_tier_omitted(self):
        data = [
            {"item": "A", "tier": "Z", "timestamp": 10},
            {"item": "B", "tier": "S", "timestamp": 20},
            {"item": "C", "timestamp": 30},
        ]
        result = assemble_tier_list({}, data, {}, None)
        assert "tier" not in result["items"][0]
        assert result["items"][1]["tier"] == "S"
        assert "tier" not in result["items"][2]

    def test_dict_wrapper_with_rankings_key(self):
        data = {
            "rankings": [
                {"item": "A", "tier": "S", "timestamp": 10},
                {"item": "B", "tier": "A", "timestamp": 20},
                {"item": "C", "tier": "B", "timestamp": 30},
            ]
        }
        result = assemble_tier_list({}, data, {}, None)
        assert len(result["items"]) == 3

    def test_drops_items_with_no_label(self):
        data = [
            {"item": "", "tier": "S", "timestamp": 10},
            {"tier": "A", "timestamp": 20},
            {"item": "Real one", "tier": "B", "timestamp": 30},
            {"item": "Real two", "tier": "C", "timestamp": 40},
            {"item": "Real three", "timestamp": 50},
        ]
        result = assemble_tier_list({}, data, {}, None)
        assert len(result["items"]) == 3

    def test_below_min_returns_none(self):
        result = assemble_tier_list({}, [{"item": "Solo"}, {"item": "Duo"}], {}, None)
        assert result is None

    def test_empty_returns_none(self):
        assert assemble_tier_list({}, [], {}, None) is None
        assert assemble_tier_list({}, None, {}, None) is None


# ─── Phase 5d: formation_diagram (sport signature component) ───


class TestAssembleFormationDiagram:
    def _positions(self) -> list[dict]:
        return [
            {"player": "Ederson", "role": "GK", "x": 50, "y": 8, "number": 31},
            {"player": "Walker", "role": "RB", "x": 80, "y": 30, "number": 2},
            {"player": "Haaland", "role": "ST", "x": 50, "y": 90, "number": 9},
        ]

    def test_normalizes_positions_from_dict(self):
        data = {"name": "4-3-3", "team": "City", "positions": self._positions()}
        result = assemble_formation_diagram({}, data, {}, None)
        assert result is not None
        assert result["name"] == "4-3-3"
        assert result["team"] == "City"
        assert len(result["positions"]) == 3
        assert result["positions"][0]["player"] == "Ederson"
        assert result["positions"][0]["x"] == 50.0
        assert result["positions"][0]["number"] == 31

    def test_accepts_bare_positions_list(self):
        result = assemble_formation_diagram({}, self._positions(), {}, None)
        assert result is not None
        assert len(result["positions"]) == 3

    def test_clamps_coordinates(self):
        data = {
            "positions": [
                {"player": "A", "x": 150, "y": -20},
                {"player": "B", "x": 50, "y": 50},
                {"player": "C", "x": 0, "y": 100},
            ]
        }
        result = assemble_formation_diagram({}, data, {}, None)
        assert result["positions"][0]["x"] == 100.0
        assert result["positions"][0]["y"] == 0.0

    def test_drops_positions_without_coords_or_name(self):
        data = {
            "positions": [
                {"player": "", "x": 10, "y": 10},
                {"player": "NoCoords"},
                {"player": "Valid1", "x": 10, "y": 10},
                {"player": "Valid2", "x": 20, "y": 20},
                {"player": "Valid3", "x": 30, "y": 30},
            ]
        }
        result = assemble_formation_diagram({}, data, {}, None)
        assert len(result["positions"]) == 3

    def test_below_min_returns_none(self):
        data = {
            "positions": [
                {"player": "A", "x": 10, "y": 10},
                {"player": "B", "x": 20, "y": 20},
            ]
        }
        assert assemble_formation_diagram({}, data, {}, None) is None

    def test_empty_returns_none(self):
        assert assemble_formation_diagram({}, [], {}, None) is None
        assert assemble_formation_diagram({}, None, {}, None) is None
        assert assemble_formation_diagram({}, {"positions": []}, {}, None) is None
