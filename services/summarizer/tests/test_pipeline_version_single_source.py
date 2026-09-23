"""PIPELINE_VERSION single-source tests (project-score-9 4.3).

The canonical pipeline version lives in
``packages/shared/src/config/pipeline-version.json`` (same cross-language
pattern as domains.json). Before 4.3 the api hardcoded ``'v2'`` (idempotency
hashes + dedupKey) while the summarizer hardcoded ``"v6"`` (Redis response
cache) — two independent knobs pretending to be one. These tests pin:

- the loader resolves the checked-in JSON (docker mount first, repo second),
- ``settings.PIPELINE_VERSION`` equals the JSON value (no silent divergence),
- the assembly phase stamps ``pipelineVersion`` on the persisted Mongo doc,
- the repository allowlist accepts the new field (silent-drop tripwire).
"""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from src.config import settings
from src.repositories.mongodb_repository import MongoDBVideoRepository
from src.shared_config.pipeline_version import get_pipeline_version

_CANONICAL_JSON = (
    Path(__file__).resolve().parent.parent.parent.parent
    / "packages"
    / "shared"
    / "src"
    / "config"
    / "pipeline-version.json"
)


def _canonical_version() -> str:
    return json.loads(_CANONICAL_JSON.read_text())["version"]


class TestSingleSource:
    def test_loader_returns_canonical_json_value(self):
        assert get_pipeline_version() == _canonical_version()

    def test_settings_reads_canonical_value(self):
        """settings.PIPELINE_VERSION must be the JSON value — not a literal."""
        assert settings.PIPELINE_VERSION == _canonical_version()

    def test_version_shape(self):
        version = get_pipeline_version()
        assert isinstance(version, str)
        assert version.startswith("v")


class TestMongoDocStamping:
    def test_repository_allowlist_accepts_pipeline_version(self):
        """save_structured_result silently drops non-allowlisted keys — the
        stamp must be allowlisted or it never reaches Mongo."""
        assert "pipelineVersion" in MongoDBVideoRepository._ALLOWED_RESULT_KEYS

    @pytest.mark.asyncio
    async def test_assembly_stamps_pipeline_version_on_saved_doc(self):
        """The persisted result dict built by the assembly phase must stamp
        the canonical version so the api's serve path can version-check it."""
        from src.services.pipeline.phases import assembly as phase

        ctx = _build_assembly_ctx()
        with (
            patch.object(phase, "assemble_response", return_value={"tabs": [], "meta": {}}),
            patch.object(
                phase,
                "settings",
                SimpleNamespace(
                    REDIS_ENABLED=False,
                    QDRANT_ENABLED=False,
                    PIPELINE_VERSION="vtest",
                ),
            ),
            patch.object(phase, "response_cache"),
        ):
            async for _ in phase.run_phase_assembly(ctx):  # type: ignore[arg-type]
                pass

        saved = ctx.repository.save_structured_result.call_args.args[1]
        assert saved["pipelineVersion"] == "vtest"


def _build_assembly_ctx() -> SimpleNamespace:
    """Minimal PipelineContext stand-in (mirrors test_phase_assembly_cache)."""
    repo = MagicMock()
    repo.save_structured_result = MagicMock(return_value=True)
    timer = MagicMock()
    timer.elapsed = MagicMock(return_value=1.0)
    return SimpleNamespace(
        video_data=SimpleNamespace(
            title="t",
            channel="c",
            duration=10,
            chapters=None,
            thumbnail_url="https://example/thumb.jpg",
        ),
        triage=SimpleNamespace(tabs=[]),
        triage_dict={},
        extraction_data={},
        enrichment_data={},
        synthesis_dict=None,
        description_analysis=None,
        scene_frames_for_assembly=None,
        scene_frames_gallery=None,
        scene_frames_all=None,
        frame_descriptions=None,
        assembled_tabs=None,
        assembled_meta=None,
        video_summary_id="vsid",
        youtube_id="ytid",
        language="en",
        is_rtl=False,
        source_language_code=None,
        clean_text="",
        repository=repo,
        timer=timer,
        transcript_data=None,
        audio_path=None,
    )
