"""Phase 7 Assembly — tab_ready streaming contract.

moment_track tabs are held back until the exact-timestamp frame fill
completes, then streamed last. Two invariants make that safe for the client:

1. Every tab_ready carries ``position`` (its index in the persisted order) so
   a late moment tab is slotted where the DB doc will have it, and the
   persisted tab dicts never gain that key.
2. Heartbeats flow while the fill runs — this phase is not under
   run_parallel_phases' keepalive, and the fill can run for minutes.
"""

from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from tests.test_phase_assembly_cache import _build_ctx


def _events(chunks: list[str]) -> list[tuple[str, dict]]:
    """Parse ``data: {"event": ..., ...}`` SSE chunks into (event, payload)."""
    parsed: list[tuple[str, dict]] = []
    for chunk in chunks:
        body = chunk.strip().removeprefix("data: ")
        if body == "[DONE]":
            continue
        payload = json.loads(body)
        parsed.append((payload.pop("event"), payload))
    return parsed


def _tabs() -> list[dict]:
    return [
        {"id": "overview", "component": "overview", "props": {}},
        {"id": "moments", "component": "moment_track", "props": {"items": []}},
        {"id": "facts", "component": "info_grid", "props": {"items": []}},
    ]


@pytest.fixture(autouse=True)
def _stub_side_effects():
    from src.services.pipeline.phases import assembly as phase

    with (
        patch.object(phase, "send_video_status_background", new=MagicMock()),
        patch.object(
            phase,
            "settings",
            SimpleNamespace(REDIS_ENABLED=False, QDRANT_ENABLED=False, PIPELINE_VERSION="vtest"),
        ),
    ):
        yield


async def _collect(gen) -> list[str]:
    return [chunk async for chunk in gen]


@pytest.mark.asyncio
async def test_tab_ready_carries_position_and_moment_tab_streams_last() -> None:
    from src.services.pipeline.phases import assembly as phase

    ctx = _build_ctx()
    tabs = _tabs()
    with (
        patch.object(phase, "assemble_response", return_value={"tabs": tabs, "meta": {}}),
        patch.object(phase, "fill_moment_frames", AsyncMock(return_value=0)) as mock_fill,
    ):
        chunks = await _collect(phase.run_phase_assembly(ctx))  # type: ignore[arg-type]

    ready = [(d["id"], d["position"]) for e, d in _events(chunks) if e == "tab_ready"]
    assert ready == [("overview", 0), ("facts", 2), ("moments", 1)]
    mock_fill.assert_awaited_once()
    # The wire hint never leaks into the persisted doc.
    assert all("position" not in t for t in tabs)


@pytest.mark.asyncio
async def test_heartbeats_flow_while_moment_fill_runs() -> None:
    from src.services.pipeline import pipeline_helpers
    from src.services.pipeline.phases import assembly as phase

    ctx = _build_ctx()

    async def slow_fill(_tabs, _yt, _hires_video) -> int:
        await asyncio.sleep(0.12)
        return 0

    with (
        patch.object(phase, "assemble_response", return_value={"tabs": _tabs(), "meta": {}}),
        patch.object(phase, "fill_moment_frames", slow_fill),
        patch.object(pipeline_helpers.settings, "SSE_HEARTBEAT_SECONDS", 0.03),
    ):
        chunks = await _collect(phase.run_phase_assembly(ctx))  # type: ignore[arg-type]

    names = [e for e, _ in _events(chunks)]
    assert names.count("heartbeat") >= 2
    # Heartbeats sit between the eager tabs and the held-back moment tab.
    assert names.index("heartbeat") > names.index("tab_ready")
    assert names[-1 - names[::-1].index("tab_ready")] == "tab_ready"
    last_ready = [d for e, d in _events(chunks) if e == "tab_ready"][-1]
    assert last_ready["id"] == "moments"


@pytest.mark.asyncio
async def test_no_moment_tabs_means_no_fill_and_no_heartbeats() -> None:
    from src.services.pipeline.phases import assembly as phase

    ctx = _build_ctx()
    tabs = [t for t in _tabs() if t["component"] != "moment_track"]
    with (
        patch.object(phase, "assemble_response", return_value={"tabs": tabs, "meta": {}}),
        patch.object(phase, "fill_moment_frames", AsyncMock(return_value=0)) as mock_fill,
    ):
        chunks = await _collect(phase.run_phase_assembly(ctx))  # type: ignore[arg-type]

    names = [e for e, _ in _events(chunks)]
    assert "heartbeat" not in names
    mock_fill.assert_not_awaited()


class _HiresVideo:
    """Records when the run's 720p file is closed relative to moment fill."""

    def __init__(self, order: list[str]) -> None:
        self._order = order

    async def close(self) -> None:
        self._order.append("close")


@pytest.mark.asyncio
async def test_moment_fill_reads_the_runs_720p_file_and_closes_it_after() -> None:
    from src.services.pipeline.phases import assembly as phase

    order: list[str] = []
    ctx = _build_ctx()
    ctx.hires_video = _HiresVideo(order)

    async def fill(_tabs, _yt, hires_video) -> int:
        order.append("fill" if hires_video is ctx.hires_video else "fill-without-file")
        return 0

    with (
        patch.object(phase, "assemble_response", return_value={"tabs": _tabs(), "meta": {}}),
        patch.object(phase, "fill_moment_frames", fill),
    ):
        await _collect(phase.run_phase_assembly(ctx))  # type: ignore[arg-type]

    assert order == ["fill", "close"]


@pytest.mark.asyncio
async def test_runs_720p_file_is_closed_without_moment_tabs() -> None:
    from src.services.pipeline.phases import assembly as phase

    order: list[str] = []
    ctx = _build_ctx()
    ctx.hires_video = _HiresVideo(order)
    tabs = [t for t in _tabs() if t["component"] != "moment_track"]
    with patch.object(phase, "assemble_response", return_value={"tabs": tabs, "meta": {}}):
        await _collect(phase.run_phase_assembly(ctx))  # type: ignore[arg-type]

    assert order == ["close"]
