"""Tests for the worker job payload model."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from src.worker.payload import VideoJobPayload, ProviderConfig
from src.worker.topology import QueueTopology, queue_arguments


VALID_PAYLOAD = {
    "videoSummaryId": "abc123def456789012345678",
    "youtubeId": "dQw4w9WgXcQ",
    "url": "https://www.youtube.com/watch?v=dQw4w9WgXcQ",
    "userId": "user-1",
    "plan": "free",
    "priority": 1,
    "providers": None,
    "bypassCache": False,
    "requestId": "req-uuid-1",
    "attempt": 1,
    "createdAt": "2026-05-18T12:00:00Z",
}


class TestVideoJobPayload:
    """VideoJobPayload mirrors the API publisher's contract."""

    def test_parses_canonical_payload(self) -> None:
        payload = VideoJobPayload.model_validate(VALID_PAYLOAD)
        assert payload.video_summary_id == "abc123def456789012345678"
        assert payload.youtube_id == "dQw4w9WgXcQ"
        assert payload.plan == "free"
        assert payload.priority == 1
        assert payload.attempt == 1

    def test_reads_a_paid_plan(self) -> None:
        payload = VideoJobPayload.model_validate({**VALID_PAYLOAD, "plan": "paid"})
        assert payload.plan == "paid"

    def test_rejects_an_unknown_plan(self) -> None:
        with pytest.raises(ValidationError):
            VideoJobPayload.model_validate({**VALID_PAYLOAD, "plan": "pro"})

    def test_accepts_a_message_published_before_the_plan_field(self) -> None:
        # P3.4 rollout: a message still in the queue carries `tier`, not `plan`.
        legacy = {key: value for key, value in VALID_PAYLOAD.items() if key != "plan"}
        payload = VideoJobPayload.model_validate({**legacy, "tier": "pro"})
        assert payload.plan == "free"

    def test_rejects_short_youtube_id(self) -> None:
        bad = {**VALID_PAYLOAD, "youtubeId": "short"}
        with pytest.raises(ValidationError):
            VideoJobPayload.model_validate(bad)

    def test_accepts_providers_object(self) -> None:
        with_providers = {
            **VALID_PAYLOAD,
            "providers": {"default": "anthropic", "fast": "openai", "fallback": None},
        }
        payload = VideoJobPayload.model_validate(with_providers)
        assert isinstance(payload.providers, ProviderConfig)
        assert payload.providers.default == "anthropic"
        assert payload.providers.fast == "openai"
        assert payload.providers.fallback is None

    def test_rejects_unknown_provider(self) -> None:
        bad = {**VALID_PAYLOAD, "providers": {"default": "made-up-provider"}}
        with pytest.raises(ValidationError):
            VideoJobPayload.model_validate(bad)

    def test_priority_clamps_to_max(self) -> None:
        too_high = {**VALID_PAYLOAD, "priority": 99}
        with pytest.raises(ValidationError):
            VideoJobPayload.model_validate(too_high)

    def test_round_trips_to_json(self) -> None:
        payload = VideoJobPayload.model_validate(VALID_PAYLOAD)
        # The API publisher serializes with camelCase keys — the model must
        # accept that input AND emit the same shape when serialized for
        # republish (retry path).
        serialized = payload.model_dump(by_alias=True, mode="json")
        assert serialized["videoSummaryId"] == VALID_PAYLOAD["videoSummaryId"]
        assert serialized["youtubeId"] == VALID_PAYLOAD["youtubeId"]
        assert serialized["bypassCache"] is False


class TestQueueTopology:
    """Topology names + queue args must match the API publisher's declarations."""

    def test_topology_names_match_publisher(self) -> None:
        assert QueueTopology.exchange == "vie.pipeline"
        assert QueueTopology.queue == "vie.pipeline.jobs"
        assert QueueTopology.routing_key == "video.process"
        assert QueueTopology.dlx == "vie.pipeline.dlx"
        assert QueueTopology.dlq == "vie.pipeline.dlq"
        assert QueueTopology.dlq_routing_key == "video.process.dead"

    def test_queue_arguments_match_publisher(self) -> None:
        args = queue_arguments()
        assert args["x-max-priority"] == 10
        assert args["x-message-ttl"] == 3_600_000
        assert args["x-dead-letter-exchange"] == "vie.pipeline.dlx"
        assert args["x-dead-letter-routing-key"] == "video.process.dead"
