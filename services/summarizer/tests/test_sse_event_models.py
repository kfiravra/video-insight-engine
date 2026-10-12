"""SSE event contract validation at sse_event() (project-score-9 4.5c).

The 2026-07-05 audit flagged that the TS side declares strict SSE event
interfaces (vie-response.ts) while Python emitted raw dicts through
``sse_event()`` — no machine check, silent drift. Now every emission is
validated against a Pydantic model in ``src/models/sse_events.py``:

- non-production (dev/test/CI): an invalid payload or unknown event name
  RAISES — drift fails the suite,
- production: it logs an error and still emits — a cosmetic contract bug
  must never kill a user's pipeline run.
"""

from __future__ import annotations

import json

import pytest

from src.models.sse_events import SSE_EVENT_MODELS, SSEEventValidationError, validate_sse_event
from src.services.pipeline.pipeline_helpers import sse_event

# Every event name the pipeline emits (grep-audited 2026-07-12; the registry
# completeness test below fails when an emitter adds a name without a model).
EXPECTED_EVENTS = {
    "cached",
    "metadata",
    "transcript_ready",
    "description_analysis",
    "triage_complete",
    "extraction_progress",
    "extraction_complete",
    "enrichment_complete",
    "synthesis_complete",
    "frames",
    "meta",
    "tab_ready",
    "complete",
    "heartbeat",
    "phase",
    "error",
    "token",
    "done",
}


class TestRegistryCompleteness:
    def test_registry_covers_every_emitted_event(self):
        assert set(SSE_EVENT_MODELS) == EXPECTED_EVENTS


class TestValidEmissions:
    def test_done_event_round_trips(self):
        chunk = sse_event("done", {"videoSummaryId": "abc", "cached": True})
        payload = json.loads(chunk.removeprefix("data: ").strip())
        assert payload == {"event": "done", "videoSummaryId": "abc", "cached": True}

    def test_complete_event(self):
        chunk = sse_event(
            "complete",
            {"tabCount": 4, "processingTimeMs": 1234, "degraded": False},
        )
        assert '"tabCount": 4' in chunk

    def test_error_event(self):
        chunk = sse_event("error", {"message": "boom", "code": "LLM_ERROR"})
        assert '"message": "boom"' in chunk

    def test_metadata_tolerates_cached_none_fields(self):
        """The cached-serve path emits entry.get(...) values that may be None
        for legacy docs — the model must accept them."""
        chunk = sse_event(
            "metadata",
            {"title": None, "channel": None, "thumbnailUrl": None, "duration": None},
        )
        assert '"event": "metadata"' in chunk

    def test_passthrough_events_accept_extra_fields(self):
        chunk = sse_event("enrichment_complete", {"quiz": [], "anythingNew": 1})
        assert '"anythingNew": 1' in chunk

    def test_phase_event(self):
        chunk = sse_event("phase", {"phase": "translation"})
        assert '"phase": "translation"' in chunk

    def test_tab_ready_should_carry_final_and_provisional_flags(self):
        chunk = sse_event("tab_ready", {"id": "m", "position": 2, "provisional": True})
        payload = json.loads(chunk.removeprefix("data: ").strip())
        assert (payload["provisional"], payload["position"]) == (True, 2)

    def test_complete_should_carry_the_final_tab_order(self):
        chunk = sse_event(
            "complete", {"tabCount": 2, "tabIds": ["overview", "code"], "processingTimeMs": 1}
        )
        assert '"tabIds": ["overview", "code"]' in chunk

    def test_tab_ready_should_carry_the_provisional_id_it_replaces(self):
        chunk = sse_event("tab_ready", {"id": "segments", "final": True, "replaces": "key_moments"})
        assert '"replaces": "key_moments"' in chunk


class TestInvalidEmissionsRaiseOutsideProduction:
    def test_unknown_event_name_raises(self):
        with pytest.raises(SSEEventValidationError, match="unknown SSE event"):
            sse_event("sponsor_segments", {"count": 1})

    def test_done_without_video_summary_id_raises(self):
        with pytest.raises(SSEEventValidationError, match="done"):
            sse_event("done", {"cached": True})

    def test_error_without_message_raises(self):
        with pytest.raises(SSEEventValidationError, match="error"):
            sse_event("error", {"code": "LLM_ERROR"})

    def test_wrong_type_raises(self):
        with pytest.raises(SSEEventValidationError, match="complete"):
            sse_event("complete", {"tabCount": "four", "processingTimeMs": 1})

    def test_provisional_final_tab_raises(self):
        with pytest.raises(SSEEventValidationError, match="provisional"):
            sse_event("tab_ready", {"id": "m", "final": True, "provisional": True})

    def test_tab_replacing_itself_raises(self):
        with pytest.raises(SSEEventValidationError, match="replaces"):
            sse_event("tab_ready", {"id": "moments", "replaces": "moments"})

    def test_provisional_tab_replacing_another_raises(self):
        with pytest.raises(SSEEventValidationError, match="replaces"):
            sse_event("tab_ready", {"id": "m", "provisional": True, "replaces": "x"})

    def test_tab_ids_disagreeing_with_tab_count_raises(self):
        with pytest.raises(SSEEventValidationError, match="tabIds"):
            sse_event("complete", {"tabCount": 3, "tabIds": ["a"], "processingTimeMs": 1})


class TestProductionFailsOpen:
    def test_invalid_payload_still_emits_in_production(self, monkeypatch, caplog):
        monkeypatch.setattr(
            "src.models.sse_events.settings.ENVIRONMENT",
            "production",
        )
        chunk = sse_event("done", {"cached": True})  # missing videoSummaryId
        assert chunk.startswith("data: ")
        assert '"event": "done"' in chunk
        assert any("sse_event_contract_violation" in r.message for r in caplog.records)

    def test_unknown_event_still_emits_in_production(self, monkeypatch, caplog):
        monkeypatch.setattr(
            "src.models.sse_events.settings.ENVIRONMENT",
            "production",
        )
        chunk = sse_event("sponsor_segments", {"count": 1})
        assert '"event": "sponsor_segments"' in chunk
        assert any("sse_event_contract_violation" in r.message for r in caplog.records)


class TestDirectValidator:
    def test_validate_returns_model_for_valid_payload(self):
        model = validate_sse_event("heartbeat", {"ts": 12.5})
        assert model is not None

    def test_docs_phantom_sponsor_segments_has_no_model(self):
        """sponsor_segments was documented but never emitted — it must NOT
        get a model (the docs row was removed; see SERVICE-SUMMARIZER.md)."""
        assert "sponsor_segments" not in SSE_EVENT_MODELS
