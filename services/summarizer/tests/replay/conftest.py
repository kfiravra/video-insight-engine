"""Shared replay fixtures: one speed-0 replay per cassette for the whole package."""

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
