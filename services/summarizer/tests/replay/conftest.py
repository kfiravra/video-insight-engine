"""Shared replay fixtures: one speed-0 replay per cassette, one scaled reference replay."""

from __future__ import annotations

import asyncio

import pytest

from tests.replay.cassette import Cassette, available_cassettes, load_cassette
from tests.replay.driver import ReplayResult, run_replay


@pytest.fixture(scope="package")
def cassettes() -> dict[str, Cassette]:
    return {video_id: load_cassette(video_id) for video_id in available_cassettes()}


@pytest.fixture(scope="package")
def replays(cassettes: dict[str, Cassette]) -> dict[str, ReplayResult]:
    """One speed-0 replay per cassette, shared by every assertion module."""
    return {vid: asyncio.run(run_replay(c, speed=0)) for vid, c in cassettes.items()}


REFERENCE_VIDEO = "T1dQhQAm8Tc"
# 1/20 of the recorded walls (~11 s); walls are rescaled by 1/speed when read.
SCALED_SPEED = 0.05


@pytest.fixture(scope="package")
def scaled_reference(cassettes: dict[str, Cassette]) -> ReplayResult:
    """T1dQhQAm8Tc at 1/20 speed — the fidelity and orchestration-timing checks share it."""
    return asyncio.run(run_replay(cassettes[REFERENCE_VIDEO], speed=SCALED_SPEED))
