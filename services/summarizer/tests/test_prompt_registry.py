"""Tests for the Langfuse-backed prompt loader and register_prompts script."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from src import config as app_config
from src.services.observability import langfuse_client as lc
from src.services.pipeline.prompt_builder import load_prompt_with_fallback


@pytest.fixture(autouse=True)
def _reset_observability(monkeypatch):
    lc._reset_for_tests()
    monkeypatch.setattr(lc.settings, "LANGFUSE_PUBLIC_KEY", None, raising=False)
    monkeypatch.setattr(lc.settings, "LANGFUSE_SECRET_KEY", None, raising=False)
    yield
    lc._reset_for_tests()


def test_load_prompt_with_fallback_uses_file_when_langfuse_disabled(tmp_path):
    """No keys → no remote prompt → file content wins."""
    f = tmp_path / "base.txt"
    f.write_text("LOCAL CONTENT")
    out = load_prompt_with_fallback(langfuse_name="summarizer:base_extraction", fallback_path=f)
    assert out == "LOCAL CONTENT"


def test_load_prompt_with_fallback_prefers_remote_when_available(tmp_path):
    """Remote prompt wins when Langfuse returns a Prompt object with text."""
    f = tmp_path / "base.txt"
    f.write_text("LOCAL CONTENT")

    class _FakePromptObj:
        prompt = "REMOTE CONTENT"
        version = 7

    with (
        patch(
            "src.services.observability.fetch_prompt_with_obj",
            return_value=_FakePromptObj(),
        ),
        patch(
            "src.services.observability.record_active_prompt",
        ),
    ):
        out = load_prompt_with_fallback(
            langfuse_name="summarizer:base_extraction",
            fallback_path=f,
        )
    assert out == "REMOTE CONTENT"


class _RemotePrompt:
    prompt = "REMOTE CONTENT"
    version = 3


def _load_with_remote_available(path: Path, fetch: MagicMock) -> str:
    with (
        patch("src.services.observability.fetch_prompt_with_obj", fetch),
        patch(
            "src.services.observability.record_active_prompt",
        ),
    ):
        return load_prompt_with_fallback(langfuse_name="summarizer:plan", fallback_path=path)


class TestPromptSource:
    """PROMPT_SOURCE=disk (0.9): dev edits to .txt files win without re-registering."""

    @pytest.fixture
    def local_prompt(self, tmp_path: Path) -> Path:
        f = tmp_path / "plan.txt"
        f.write_text("LOCAL CONTENT")
        return f

    def test_should_never_call_langfuse_when_prompt_source_is_disk(
        self, monkeypatch: pytest.MonkeyPatch, local_prompt: Path
    ) -> None:
        monkeypatch.setattr(app_config.settings, "PROMPT_SOURCE", "disk")
        monkeypatch.setattr(app_config.settings, "ENVIRONMENT", "development")
        fetch = MagicMock(return_value=_RemotePrompt())

        out = _load_with_remote_available(local_prompt, fetch)

        assert out == "LOCAL CONTENT"
        fetch.assert_not_called()

    def test_should_ignore_disk_mode_when_environment_is_production(
        self, monkeypatch: pytest.MonkeyPatch, local_prompt: Path
    ) -> None:
        monkeypatch.setattr(app_config.settings, "PROMPT_SOURCE", "disk")
        monkeypatch.setattr(app_config.settings, "ENVIRONMENT", "production")

        out = _load_with_remote_available(local_prompt, MagicMock(return_value=_RemotePrompt()))

        assert out == "REMOTE CONTENT"

    def test_should_prefer_registry_by_default(self, local_prompt: Path) -> None:
        assert app_config.settings.PROMPT_SOURCE == "registry"

        out = _load_with_remote_available(local_prompt, MagicMock(return_value=_RemotePrompt()))

        assert out == "REMOTE CONTENT"


def test_load_prompt_with_fallback_handles_fetch_exception(tmp_path):
    """Any exception inside the registry fetch is swallowed; falls back to file."""
    f = tmp_path / "base.txt"
    f.write_text("LOCAL CONTENT")
    with patch(
        "src.services.observability.fetch_prompt_with_obj",
        side_effect=RuntimeError("network"),
    ):
        out = load_prompt_with_fallback(
            langfuse_name="summarizer:base_extraction",
            fallback_path=f,
        )
    assert out == "LOCAL CONTENT"


def test_load_prompt_with_fallback_records_prompt_obj(tmp_path):
    """Successful registry fetch records the Prompt object for native linkage."""
    f = tmp_path / "base.txt"
    f.write_text("LOCAL CONTENT")

    class _FakePromptObj:
        prompt = "REMOTE CONTENT"
        version = 3

    obj = _FakePromptObj()
    with (
        patch(
            "src.services.observability.fetch_prompt_with_obj",
            return_value=obj,
        ),
        patch(
            "src.services.observability.record_active_prompt",
        ) as mock_record,
    ):
        load_prompt_with_fallback(
            langfuse_name="summarizer:base_extraction",
            fallback_path=f,
        )
    mock_record.assert_called_once_with("summarizer:base_extraction", obj)


def _load_register_script():
    """Import scripts/register_prompts.py as a module without running it."""
    repo_root = Path(__file__).resolve().parents[3]
    script_path = repo_root / "scripts" / "register_prompts.py"
    spec = importlib.util.spec_from_file_location("register_prompts_test", script_path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules["register_prompts_test"] = module
    spec.loader.exec_module(module)
    return module


def test_register_prompts_discovers_summarizer_prompts():
    """The discovery walks the prompts tree and produces sensible names."""
    mod = _load_register_script()
    records = mod.discover_prompts()
    names = {r.name for r in records}
    # Sanity checks — these files exist in the repo.
    assert "summarizer:base_extraction" in names
    assert any(n.startswith("summarizer:schema:") for n in names)
    assert any(n.startswith("summarizer:enrich:") for n in names)


def test_register_prompts_naming_uses_subdir_label():
    mod = _load_register_script()
    repo_root = Path(__file__).resolve().parents[3]
    root = repo_root / "services" / "summarizer" / "src" / "prompts"
    sample = root / "schemas" / "food.txt"
    assert mod._name_for("summarizer", root, sample) == "summarizer:schema:food"


def test_register_prompts_already_synced_returns_true_on_match():
    """The idempotency check should skip uploads when content matches."""
    mod = _load_register_script()

    class _FakeExisting:
        prompt = "hello world"

    class _FakeClient:
        # _already_synced scopes the lookup to the production label, so the
        # fake must accept the `label` kwarg the real client receives.
        def get_prompt(self, name: str, label: str | None = None):  # noqa: ARG002
            return _FakeExisting()

    assert mod._already_synced(_FakeClient(), "x", "hello world") is True
    assert mod._already_synced(_FakeClient(), "x", "different") is False


# ─── Safety allowlist for the uploader (added 2026-05-20 sign-off) ──────
def test_register_prompts_rejects_oversized_file():
    """Files larger than the cap are skipped."""
    mod = _load_register_script()
    ok, reason = mod._is_safe_to_upload("x" * (mod._MAX_PROMPT_BYTES + 1), Path("x.txt"))
    assert ok is False
    assert "byte cap" in reason


def test_register_prompts_rejects_empty_file():
    mod = _load_register_script()
    ok, _ = mod._is_safe_to_upload("   \n", Path("x.txt"))
    assert ok is False


def test_register_prompts_rejects_aws_key_leak():
    mod = _load_register_script()
    ok, reason = mod._is_safe_to_upload(
        "Example prompt with AKIAIOSFODNN7EXAMPLE embedded by accident",
        Path("x.txt"),
    )
    assert ok is False
    assert "secret pattern" in reason


def test_register_prompts_rejects_anthropic_key_leak():
    mod = _load_register_script()
    ok, _ = mod._is_safe_to_upload(
        "Some text and sk-ant-api03-xxxxxxxxxxxxxxxxxxxxxxxxabcdef done",
        Path("x.txt"),
    )
    assert ok is False


def test_register_prompts_rejects_env_var_name_in_body():
    """An env-var name (LANGFUSE_SECRET_KEY) in the file is a strong signal of leak."""
    mod = _load_register_script()
    ok, _ = mod._is_safe_to_upload(
        "Some text LANGFUSE_SECRET_KEY=abc123 ...",
        Path("x.txt"),
    )
    assert ok is False


def test_register_prompts_accepts_normal_prompt():
    mod = _load_register_script()
    ok, reason = mod._is_safe_to_upload(
        "Extract the most useful information from this transcript.",
        Path("x.txt"),
    )
    assert ok is True
    assert reason == "ok"


# ─── prompt_builder centralized loader ──────────────────────────────────
def test_load_prompt_text_routes_through_registry_for_prompts_dir(monkeypatch):
    """A path under PROMPTS_DIR is looked up against the registry."""
    from src.services.pipeline import prompt_builder

    fake_path = prompt_builder.PROMPTS_DIR / "synthesis.txt"
    captured: dict[str, str] = {}

    class _FakePromptObj:
        prompt = "REGISTRY VERSION"
        version = 1

    def fake_fetch(name: str) -> object | None:
        captured["name"] = name
        return _FakePromptObj()

    monkeypatch.setattr(
        "src.services.observability.fetch_prompt_with_obj",
        fake_fetch,
    )
    monkeypatch.setattr(
        "src.services.observability.record_active_prompt",
        lambda *_args, **_kwargs: None,
    )
    result = prompt_builder.load_prompt_text(fake_path)
    assert result == "REGISTRY VERSION"
    assert captured["name"] == "summarizer:synthesis"


def test_load_prompt_text_rejects_paths_outside_prompts_dir(tmp_path, caplog):
    """Paths outside PROMPTS_DIR are rejected with a warning and empty result.

    Matches the prior path-safety contract from ``enrichment._load_prompt`` —
    we don't read arbitrary files just because a caller asked nicely.
    """
    import logging
    from src.services.pipeline import prompt_builder

    f = tmp_path / "random.txt"
    f.write_text("LOCAL ONLY")
    with caplog.at_level(logging.WARNING):
        out = prompt_builder.load_prompt_text(f)
    assert out == ""
    assert any("outside PROMPTS_DIR" in r.message for r in caplog.records)


def test_langfuse_name_for_schema_path():
    """schemas/food.txt → summarizer:schema:food."""
    from src.services.pipeline.prompt_builder import _langfuse_name_for, PROMPTS_DIR

    assert _langfuse_name_for(PROMPTS_DIR / "schemas" / "food.txt") == "summarizer:schema:food"
    assert _langfuse_name_for(PROMPTS_DIR / "plan.txt") == "summarizer:plan"
    assert _langfuse_name_for(Path("/tmp/random.txt")) is None
