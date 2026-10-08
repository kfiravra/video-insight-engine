"""Vision batching (pipeline-1min 1d.4): batch plan, index mapping, concurrency, retry, budgets."""

from __future__ import annotations

import asyncio
import json
import re
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest
from litellm.exceptions import BadRequestError, RateLimitError

from src.services.media import frame_analyzer
from src.services.media.frame_analyzer import (
    PROMPT_PATH,
    analyze_frames_with_vision,
    batch_max_tokens,
    plan_vision_batches,
)
from src.services.pipeline.prompt_registry import declared_placeholders

_LABEL_RE = re.compile(r"^Frame (\d+) \(at (\d+:\d{2})\):$")


@pytest.fixture(autouse=True)
def _no_retry_pause(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(frame_analyzer, "_RETRY_PAUSE_SECONDS", 0.0)


@pytest.fixture
def parallel_on(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(frame_analyzer.settings, "FRAME_VISION_PARALLEL", True)


def _frames(tmp_path: Path, count: int, *, text_score: float = 0.0) -> list[dict]:
    """``count`` frames 10 s apart, scored so the score order is NOT the time order."""
    frames = []
    for i in range(count):
        path = tmp_path / f"scene_{i:04d}.jpg"
        path.write_bytes(b"\xff\xd8\xff\xe0" + bytes([i % 256]) * 64)
        frames.append(
            {
                "path": str(path),
                "index": i,
                "timestamp": float(10 * (i + 1)),
                "total_score": ((i * 7) % count) / count + 0.01,
                "text_score": text_score,
                "s3_url": f"https://s3/{i}.jpg",
            }
        )
    return frames


def _labels(messages: list[dict[str, Any]]) -> list[tuple[int, str]]:
    """(label number, m:ss) of every image label in one call's content."""
    found = []
    for block in messages[0]["content"]:
        match = _LABEL_RE.match(block.get("text", "")) if block.get("type") == "text" else None
        if match:
            found.append((int(match.group(1)), match.group(2)))
    return found


def _echo_reply(messages: list[dict[str, Any]]) -> str:
    """A reply in REVERSE label order whose content names the label's m:ss."""
    items = [
        {"frame_index": number, "scene_type": "other", "content": stamp, "text_visible": ""}
        for number, stamp in reversed(_labels(messages))
    ]
    return json.dumps(items)


def _mmss(seconds: float) -> str:
    return f"{int(seconds) // 60}:{int(seconds) % 60:02d}"


class TestPlanVisionBatches:
    @pytest.mark.parametrize(
        ("count", "sizes"),
        [(40, [8, 8, 8, 8, 8]), (25, [7, 6, 6, 6]), (8, [4, 4]), (5, [3, 2]), (4, [4]), (1, [1])],
    )
    def test_should_split_into_calls_of_at_most_eight_when_batching_is_on(
        self, tmp_path: Path, parallel_on: None, count: int, sizes: list[int]
    ) -> None:
        batches = plan_vision_batches(_frames(tmp_path, count))

        assert [len(b) for b in batches] == sizes

    def test_should_cover_every_frame_once_in_time_order_per_call(
        self, tmp_path: Path, parallel_on: None
    ) -> None:
        frames = _frames(tmp_path, 25)

        batches = plan_vision_batches(frames)

        indices = sorted(f["index"] for batch in batches for f in batch)
        assert indices == list(range(25))
        for batch in batches:
            stamps = [f["timestamp"] for f in batch]
            assert stamps == sorted(stamps)

    def test_should_stride_frames_so_each_call_spans_the_video(
        self, tmp_path: Path, parallel_on: None
    ) -> None:
        batches = plan_vision_batches(_frames(tmp_path, 40))

        assert [f["index"] for f in batches[0]] == [0, 5, 10, 15, 20, 25, 30, 35]

    def test_should_plan_one_call_when_batching_is_off(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(frame_analyzer.settings, "FRAME_VISION_PARALLEL", False)

        frames = _frames(tmp_path, 40)

        batches = plan_vision_batches(frames)

        assert batches == [frames]  # today's single call, frames in the given (score) order


class TestIndexMapping:
    async def test_should_map_each_calls_labels_back_to_its_own_frames_when_calls_finish_out_of_order(
        self, tmp_path: Path, parallel_on: None
    ) -> None:
        frames = _frames(tmp_path, 40)
        provider = MagicMock()

        async def reply(messages: list[dict[str, Any]], **kwargs: Any) -> str:
            # Later batches answer first.
            await asyncio.sleep(
                0.01 * (kwargs["span_metadata"]["batches"] - kwargs["span_metadata"]["batch"])
            )
            return _echo_reply(messages)

        provider.complete_with_messages = AsyncMock(side_effect=reply)

        descriptions = await analyze_frames_with_vision(frames, provider, max_frames=40)

        assert len(descriptions) == 40
        by_original = {d["original_index"]: d for d in descriptions}
        for frame in frames:
            described = by_original[frame["index"]]
            assert described["content"] == _mmss(frame["timestamp"])
            assert described["timestamp_sec"] == frame["timestamp"]
            assert described["s3_url"] == frame["s3_url"]

    async def test_should_return_descriptions_in_time_order_with_global_indices(
        self, tmp_path: Path, parallel_on: None
    ) -> None:
        provider = MagicMock()
        provider.complete_with_messages = AsyncMock(side_effect=lambda m, **_: _echo_reply(m))

        descriptions = await analyze_frames_with_vision(
            _frames(tmp_path, 8), provider, max_frames=8
        )

        assert [d["frame_index"] for d in descriptions] == list(range(8))
        stamps = [d["timestamp_sec"] for d in descriptions]
        assert stamps == sorted(stamps)

    async def test_should_label_every_call_from_frame_zero_and_state_its_count(
        self, tmp_path: Path, parallel_on: None
    ) -> None:
        provider = MagicMock()
        provider.complete_with_messages = AsyncMock(side_effect=lambda m, **_: _echo_reply(m))

        await analyze_frames_with_vision(_frames(tmp_path, 8), provider, max_frames=8)

        for call in provider.complete_with_messages.call_args_list:
            messages = call.args[0]
            assert [n for n, _ in _labels(messages)] == [0, 1, 2, 3]
            assert messages[0]["content"][-1]["text"].startswith("This request has 4 frames")


class TestConcurrency:
    async def _peak_in_flight(self, tmp_path: Path, frame_count: int) -> tuple[int, int]:
        in_flight = 0
        peak = 0

        async def reply(messages: list[dict[str, Any]], **_: Any) -> str:
            nonlocal in_flight, peak
            in_flight += 1
            peak = max(peak, in_flight)
            await asyncio.sleep(0.02)
            in_flight -= 1
            return _echo_reply(messages)

        provider = MagicMock()
        provider.complete_with_messages = AsyncMock(side_effect=reply)
        await analyze_frames_with_vision(_frames(tmp_path, frame_count), provider, max_frames=40)
        return peak, provider.complete_with_messages.await_count

    async def test_should_never_run_more_calls_at_once_than_the_limit(
        self, tmp_path: Path, parallel_on: None, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(frame_analyzer, "VISION_MAX_PARALLEL", 2)

        peak, calls = await self._peak_in_flight(tmp_path, 40)

        assert (peak, calls) == (2, 5)

    async def test_should_run_all_five_calls_at_once_by_default(
        self, tmp_path: Path, parallel_on: None
    ) -> None:
        peak, calls = await self._peak_in_flight(tmp_path, 40)

        assert (peak, calls) == (5, 5)


class TestFailuresAndRetry:
    def _provider(self, side_effect: Any) -> MagicMock:
        provider = MagicMock()
        provider.model = "anthropic/claude-sonnet-4-6"
        provider.complete_with_messages = AsyncMock(side_effect=side_effect)
        return provider

    async def test_should_keep_other_calls_descriptions_when_one_call_fails_twice(
        self, tmp_path: Path, parallel_on: None
    ) -> None:
        async def reply(messages: list[dict[str, Any]], **kwargs: Any) -> str:
            if kwargs["span_metadata"]["batch"] == 1:
                raise asyncio.TimeoutError
            return _echo_reply(messages)

        provider = self._provider(reply)

        descriptions = await analyze_frames_with_vision(
            _frames(tmp_path, 8), provider, max_frames=8
        )

        assert len(descriptions) == 4
        assert provider.complete_with_messages.await_count == 3  # batch 1 twice, batch 2 once

    async def test_should_retry_once_after_a_rate_limit_honouring_retry_after(
        self, tmp_path: Path
    ) -> None:
        response = httpx.Response(
            429, headers={"retry-after": "7"}, request=httpx.Request("POST", "https://llm")
        )
        limited = RateLimitError("slow down", "anthropic", "claude", response=response)
        provider = self._provider([limited, json.dumps([{"frame_index": 0, "content": "ok"}])])

        with patch.object(frame_analyzer.asyncio, "sleep", new=AsyncMock()) as sleep:
            descriptions = await analyze_frames_with_vision(_frames(tmp_path, 1), provider)

        assert [d["content"] for d in descriptions] == ["ok"]
        sleep.assert_awaited_once_with(7.0)

    async def test_should_stop_after_two_attempts_when_every_attempt_times_out(
        self, tmp_path: Path
    ) -> None:
        provider = self._provider(asyncio.TimeoutError)

        descriptions = await analyze_frames_with_vision(_frames(tmp_path, 1), provider)

        assert descriptions == []
        assert provider.complete_with_messages.await_count == 2

    async def test_should_not_retry_when_the_request_is_rejected(self, tmp_path: Path) -> None:
        response = httpx.Response(400, request=httpx.Request("POST", "https://llm"))
        provider = self._provider(
            BadRequestError("bad image", "claude", "anthropic", response=response)
        )

        descriptions = await analyze_frames_with_vision(_frames(tmp_path, 1), provider)

        assert descriptions == []
        assert provider.complete_with_messages.await_count == 1

    async def test_should_retry_an_unparseable_reply_with_a_larger_token_ceiling(
        self, tmp_path: Path
    ) -> None:
        provider = self._provider(['[{"frame_index": 0, "content": "cut', '[{"frame_index": 0}]'])

        descriptions = await analyze_frames_with_vision(_frames(tmp_path, 1), provider)

        assert len(descriptions) == 1
        first, second = (
            c.kwargs["max_tokens"] for c in provider.complete_with_messages.call_args_list
        )
        assert second == int(first * 1.5)

    async def test_should_tag_each_call_with_its_batch_and_attempt(
        self, tmp_path: Path, parallel_on: None
    ) -> None:
        provider = self._provider(lambda m, **_: _echo_reply(m))

        await analyze_frames_with_vision(_frames(tmp_path, 8), provider, max_frames=8)

        metadata = [
            c.kwargs["span_metadata"] for c in provider.complete_with_messages.call_args_list
        ]
        assert sorted(m["batch"] for m in metadata) == [1, 2]
        assert all(m["batches"] == 2 and m["attempt"] == 1 for m in metadata)
        assert all(
            c.kwargs["span_name"] == "frame_vision"
            for c in provider.complete_with_messages.call_args_list
        )


class TestTokenBudget:
    def test_should_give_text_heavy_frames_the_larger_budget(self, tmp_path: Path) -> None:
        plain = batch_max_tokens(_frames(tmp_path, 8))
        text = batch_max_tokens(_frames(tmp_path, 8, text_score=0.3))

        assert (plain, text) == (2200, 4200)

    def test_should_keep_eight_plain_frames_above_the_old_floor(self, tmp_path: Path) -> None:
        assert batch_max_tokens(_frames(tmp_path, 8)) >= 2000

    def test_should_never_go_below_the_minimum_for_a_small_call(self, tmp_path: Path) -> None:
        assert batch_max_tokens(_frames(tmp_path, 1)) == 1000


class TestVisionPrompt:
    def test_should_ship_a_prompt_file_without_placeholders(self) -> None:
        text = PROMPT_PATH.read_text()

        assert declared_placeholders(text) == frozenset()
        assert "frame_index is the N from that frame's label" in text

    async def test_should_send_the_registry_loaded_prompt_first(self, tmp_path: Path) -> None:
        provider = MagicMock()
        provider.complete_with_messages = AsyncMock(return_value='[{"frame_index": 0}]')

        with patch.object(
            frame_analyzer, "load_prompt_text", return_value="REGISTRY PROMPT"
        ) as load:
            await analyze_frames_with_vision(_frames(tmp_path, 1), provider)

        load.assert_called_once_with(PROMPT_PATH)
        content = provider.complete_with_messages.call_args.args[0][0]["content"]
        assert content[0] == {"type": "text", "text": "REGISTRY PROMPT"}
