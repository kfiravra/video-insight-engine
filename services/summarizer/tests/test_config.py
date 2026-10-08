"""Tests for the Settings surface: defaults, env parsing and compose passthrough parity."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import pytest
import yaml
from pydantic import TypeAdapter

from src.config import Settings, settings

_REPO_ROOT = Path(__file__).resolve().parents[3]
_COMPOSE_FILES = ("docker-compose.yml", "docker-compose.prod.yml")
# Passed through both x-summarizer-env anchors (pipeline-1min 1d.6). A setting
# that compose does not pass silently keeps its code default in containers, and
# a compose default that differs from config.py makes the two disagree.
_PASSED_THROUGH = (
    "EXTRACTION_PARALLEL",
    "EXTRACTION_PARALLEL_BATCHES",
    "CHUNKED_EXTRACTION_THRESHOLD",
    "MAX_TOKENS_PER_BATCH",
    "MAX_MINUTES_PER_BATCH",
    "EXTRACTION_FORCE_SPLIT_CHUNKS",
    "FRAME_VISION_ENABLED",
    "FRAME_VISION_PARALLEL",
    "FRAME_TIER_ENABLED",
    "LLM_VISION_MODEL",
)
# Deleted settings must not linger in config.py or either compose anchor.
_DELETED = (
    "CHAPTER_BATCH_SIZE",
    "CHUNKED_EXTRACTION_TIMEOUT",
    "MAX_CHAPTER_CHARS",
    "MAX_TRANSCRIPT_CHARS",
)
_COMPOSE_PASSTHROUGH = re.compile(r"^\$\{(?P<name>\w+):-(?P<default>.*)\}$")


@pytest.fixture
def isolated_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> pytest.MonkeyPatch:
    """No touched setting in the environment and no ``./.env`` in reach of Settings()."""
    monkeypatch.chdir(tmp_path)
    for name in _PASSED_THROUGH:
        monkeypatch.delenv(name, raising=False)
    return monkeypatch


def _summarizer_env(compose_file: str) -> dict[str, Any]:
    path = _REPO_ROOT / compose_file
    if not path.is_file():
        pytest.skip(f"{compose_file} is not available here (container run)")
    return yaml.safe_load(path.read_text())["x-summarizer-env"]


def _compose_default(name: str, raw_default: str) -> Any:
    """The value a blank-env container gets: ``""`` → None, else parsed like env."""
    if raw_default == "":
        return None
    return TypeAdapter(Settings.model_fields[name].annotation).validate_python(raw_default)


class TestSettingsSurface:
    def test_vestigial_prompt_version_removed(self) -> None:
        """PROMPT_VERSION was declared but never read anywhere — deleted."""
        assert not hasattr(Settings, "PROMPT_VERSION")

    def test_pipeline_version_still_present(self) -> None:
        """PIPELINE_VERSION is live (Redis response-cache key) and must stay."""
        assert isinstance(Settings().PIPELINE_VERSION, str)
        assert Settings().PIPELINE_VERSION.startswith("v")

    def test_worker_settings_exist_with_sensible_defaults(self) -> None:
        assert settings.RABBITMQ_URL
        assert settings.WORKER_CONCURRENCY >= 1
        assert settings.WORKER_MAX_RETRIES >= 1
        assert settings.WORKER_PREFETCH >= 1

    @pytest.mark.parametrize("name", _DELETED)
    def test_should_not_declare_a_deleted_setting(self, name: str) -> None:
        assert name not in Settings.model_fields


class TestConcurrencyDefaults:
    def test_should_run_extraction_batches_in_parallel_when_env_is_unset(
        self, isolated_env: pytest.MonkeyPatch
    ) -> None:
        assert Settings().EXTRACTION_PARALLEL is True

    def test_should_run_vision_batches_in_parallel_when_env_is_unset(
        self, isolated_env: pytest.MonkeyPatch
    ) -> None:
        assert Settings().FRAME_VISION_PARALLEL is True

    def test_should_allow_six_extraction_calls_in_flight_when_env_is_unset(
        self, isolated_env: pytest.MonkeyPatch
    ) -> None:
        assert Settings().EXTRACTION_PARALLEL_BATCHES == 6

    def test_should_disable_parallel_extraction_when_env_is_false(
        self, isolated_env: pytest.MonkeyPatch
    ) -> None:
        isolated_env.setenv("EXTRACTION_PARALLEL", "false")
        assert Settings().EXTRACTION_PARALLEL is False

    def test_should_disable_parallel_vision_when_env_is_false(
        self, isolated_env: pytest.MonkeyPatch
    ) -> None:
        isolated_env.setenv("FRAME_VISION_PARALLEL", "false")
        assert Settings().FRAME_VISION_PARALLEL is False

    def test_should_route_vision_to_the_primary_model_when_no_override_is_set(
        self, isolated_env: pytest.MonkeyPatch
    ) -> None:
        # None → frame_analyzer keeps the caller's primary-model provider.
        assert Settings().get_stage_model("vision") is None


class TestComposePassthrough:
    @pytest.mark.parametrize("name", _PASSED_THROUGH)
    @pytest.mark.parametrize("compose_file", _COMPOSE_FILES)
    def test_should_pass_setting_through_compose_with_the_config_default(
        self, compose_file: str, name: str
    ) -> None:
        match = _COMPOSE_PASSTHROUGH.match(str(_summarizer_env(compose_file).get(name, "")))
        assert match and match["name"] == name, f"{compose_file} does not pass {name} through"
        assert _compose_default(name, match["default"]) == Settings.model_fields[name].default

    @pytest.mark.parametrize("name", _DELETED)
    @pytest.mark.parametrize("compose_file", _COMPOSE_FILES)
    def test_should_not_pass_a_deleted_setting_through_compose(
        self, compose_file: str, name: str
    ) -> None:
        assert name not in _summarizer_env(compose_file)
