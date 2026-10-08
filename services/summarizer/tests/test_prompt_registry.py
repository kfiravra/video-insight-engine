"""Tests for the Langfuse-backed prompt loader and register_prompts script."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from src import config as app_config
from src.services.observability import langfuse_client as lc
from src.services.pipeline import prompt_builder
from src.services.pipeline.prompt_builder import load_prompt_with_fallback
from src.services.transcription import transcript_chunker


@pytest.fixture(autouse=True)
def _pin_registry_prompt_source(monkeypatch: pytest.MonkeyPatch) -> None:
    """Registry-mode tests must not depend on a dev's exported PROMPT_SOURCE=disk."""
    monkeypatch.setattr(app_config.settings, "PROMPT_SOURCE", "registry")


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

    @pytest.mark.parametrize("environment", ["production", "staging", "prd", "live", "demo"])
    def test_should_ignore_disk_mode_when_environment_is_not_a_dev_name(
        self, monkeypatch: pytest.MonkeyPatch, local_prompt: Path, environment: str
    ) -> None:
        monkeypatch.setattr(app_config.settings, "PROMPT_SOURCE", "disk")
        monkeypatch.setattr(app_config.settings, "ENVIRONMENT", environment)

        out = _load_with_remote_available(local_prompt, MagicMock(return_value=_RemotePrompt()))

        assert out == "REMOTE CONTENT"

    def test_should_default_to_registry(self) -> None:
        assert app_config.Settings.model_fields["PROMPT_SOURCE"].default == "registry"

    def test_should_prefer_registry_when_prompt_source_is_registry(
        self, local_prompt: Path
    ) -> None:
        out = _load_with_remote_available(local_prompt, MagicMock(return_value=_RemotePrompt()))

        assert out == "REMOTE CONTENT"

    def test_should_reread_edited_file_when_prompt_source_is_disk(
        self, monkeypatch: pytest.MonkeyPatch, local_prompt: Path
    ) -> None:
        monkeypatch.setattr(app_config.settings, "PROMPT_SOURCE", "disk")
        monkeypatch.setattr(app_config.settings, "ENVIRONMENT", "development")
        load_prompt_with_fallback(langfuse_name="summarizer:plan", fallback_path=local_prompt)
        local_prompt.write_text("EDITED CONTENT")

        out = load_prompt_with_fallback(langfuse_name="summarizer:plan", fallback_path=local_prompt)

        assert out == "EDITED CONTENT"

    def test_should_keep_process_cache_when_prompt_source_is_registry(
        self, local_prompt: Path
    ) -> None:
        # Langfuse is disabled by the autouse fixture → file fallback path.
        load_prompt_with_fallback(langfuse_name="summarizer:plan", fallback_path=local_prompt)
        local_prompt.write_text("EDITED CONTENT")

        out = load_prompt_with_fallback(langfuse_name="summarizer:plan", fallback_path=local_prompt)

        assert out == "LOCAL CONTENT"


class TestChapterDetectPromptSource:
    """The chunker's module-global prompt cache honours PROMPT_SOURCE=disk too."""

    @pytest.fixture(autouse=True)
    def _empty_cache(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(transcript_chunker, "_CHAPTER_DETECT_PROMPT", None)

    def _load_twice(self, monkeypatch: pytest.MonkeyPatch) -> list[str | None]:
        loader = MagicMock(side_effect=["FIRST", "SECOND"])
        monkeypatch.setattr(prompt_builder, "load_prompt_text", loader)
        return [transcript_chunker._chapter_detect_prompt() for _ in range(2)]

    def test_should_reload_on_every_call_when_prompt_source_is_disk(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(app_config.settings, "PROMPT_SOURCE", "disk")
        monkeypatch.setattr(app_config.settings, "ENVIRONMENT", "development")

        assert (self._load_twice(monkeypatch), transcript_chunker._CHAPTER_DETECT_PROMPT) == (
            ["FIRST", "SECOND"],
            None,
        )

    def test_should_cache_for_process_when_prompt_source_is_registry(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        assert self._load_twice(monkeypatch) == ["FIRST", "FIRST"]


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
    assert "summarizer:enrich_quiz" in names


def test_register_prompts_naming_uses_subdir_label():
    mod = _load_register_script()
    repo_root = Path(__file__).resolve().parents[3]
    root = repo_root / "services" / "summarizer" / "src" / "prompts"
    sample = root / "schemas" / "food.txt"
    assert mod._name_for("summarizer", root, sample) == "summarizer:schema:food"


def test_register_prompts_sync_state_should_compare_the_labelled_content():
    """The idempotency check should skip uploads when content matches."""
    mod = _load_register_script()

    class _FakeExisting:
        prompt = "hello world"

    class _FakeClient:
        # _sync_state scopes the lookup to the production label, so the
        # fake must accept the `label` kwarg the real client receives.
        def get_prompt(self, name: str, label: str | None = None):  # noqa: ARG002
            return _FakeExisting()

    states = [mod._sync_state(_FakeClient(), "x", text) for text in ("hello world", "different")]

    assert states == ["unchanged", "changed"]


# ─── --label / --dry-run (stage prompts without moving `production`) ────
class _NotFound(Exception):
    """The registry's 404 — what the Langfuse SDK raises for a missing label."""

    status_code = 404


class _FakeRegistry:
    """Serves text per label and records every get/create call."""

    def __init__(self, by_label: dict[str, str] | None = None) -> None:
        self.by_label = by_label or {}
        self.gets: list[tuple[str, str | None]] = []
        self.creates: list[dict[str, object]] = []

    def get_prompt(self, name: str, *, label: str | None = None) -> object:
        self.gets.append((name, label))
        if label not in self.by_label:
            raise _NotFound(f"no version of {name} carries label {label}")
        return MagicMock(prompt=self.by_label[label])

    def create_prompt(self, *, name: str, prompt: str, labels: list[str]) -> object:
        self.creates.append({"name": name, "prompt": prompt, "labels": labels})
        return MagicMock()


def _run_main(monkeypatch, mod, registry: _FakeRegistry, argv: list[str]) -> int:
    """Run main() over one fake record with the Langfuse client replaced."""
    record = mod.PromptRecord(name="summarizer:plan", content="NEW", source_path=Path("plan.txt"))
    monkeypatch.setattr(mod, "discover_prompts", lambda: [record])
    monkeypatch.setattr(mod, "_build_client", lambda *, dry_run: registry)
    return mod.main(argv)


def test_register_prompts_should_upload_with_requested_label_when_label_given(monkeypatch):
    mod = _load_register_script()
    registry = _FakeRegistry(by_label={"production": "OLD"})

    _run_main(monkeypatch, mod, registry, ["--commit", "--label", "pipeline-1min"])

    assert registry.creates == [
        {"name": "summarizer:plan", "prompt": "NEW", "labels": ["pipeline-1min"]}
    ]


def test_register_prompts_should_compare_against_requested_label_when_label_given(monkeypatch):
    mod = _load_register_script()
    registry = _FakeRegistry(by_label={"production": "OLD", "pipeline-1min": "NEW"})

    _run_main(monkeypatch, mod, registry, ["--commit", "--label", "pipeline-1min"])

    assert registry.gets == [("summarizer:plan", "pipeline-1min")]
    assert registry.creates == []


def test_register_prompts_should_upload_with_production_label_when_no_label_given(monkeypatch):
    mod = _load_register_script()
    registry = _FakeRegistry(by_label={"production": "OLD"})

    _run_main(monkeypatch, mod, registry, ["--commit"])

    assert registry.gets == [("summarizer:plan", "production")]
    assert registry.creates[0]["labels"] == ["production"]


@pytest.mark.parametrize("argv", [["--dry-run"], [], ["--dry-run", "--label", "pipeline-1min"]])
def test_register_prompts_should_upload_nothing_when_dry_run(monkeypatch, argv):
    mod = _load_register_script()
    registry = _FakeRegistry(by_label={"production": "OLD"})

    assert _run_main(monkeypatch, mod, registry, argv) == 0
    assert registry.creates == []


def test_register_prompts_dry_run_should_report_changes_against_the_label_when_client_given():
    mod = _load_register_script()
    registry = _FakeRegistry(by_label={"production": "SAME"})
    same = mod.PromptRecord(name="a", content="SAME", source_path=Path("a.txt"))
    changed = mod.PromptRecord(name="b", content="NEW", source_path=Path("b.txt"))

    results = [mod.upload_prompt(registry, r, dry_run=True) for r in (same, changed)]

    assert results == ["unchanged", "would-upload"]


def test_register_prompts_dry_run_should_report_would_upload_when_no_client():
    mod = _load_register_script()
    record = mod.PromptRecord(name="a", content="X", source_path=Path("a.txt"))

    assert mod.upload_prompt(None, record, dry_run=True) == "would-upload"


def test_register_prompts_dry_run_should_skip_langfuse_when_keys_blank(monkeypatch):
    mod = _load_register_script()
    monkeypatch.delenv("LANGFUSE_PUBLIC_KEY", raising=False)
    monkeypatch.delenv("LANGFUSE_SECRET_KEY", raising=False)

    assert mod._build_client(dry_run=True) is None


@pytest.mark.parametrize("label", ["latest", "Production", "pipeline 1min", ""])
def test_register_prompts_should_reject_label_when_reserved_or_malformed(label):
    mod = _load_register_script()

    with pytest.raises(SystemExit):
        mod._parse_args(["--commit", "--label", label])


def test_register_prompts_should_reject_commit_and_dry_run_together():
    mod = _load_register_script()

    with pytest.raises(SystemExit):
        mod._parse_args(["--commit", "--dry-run"])


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
    # Same placeholders as the shipped file — a different set falls back to disk.
    registry_text = fake_path.read_text() + "\nREGISTRY VERSION"

    class _FakePromptObj:
        prompt = registry_text
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
    assert result == registry_text
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
    from src.services.pipeline.prompt_builder import PROMPTS_DIR, _langfuse_name_for

    assert _langfuse_name_for(PROMPTS_DIR / "schemas" / "food.txt") == "summarizer:schema:food"
    assert _langfuse_name_for(PROMPTS_DIR / "plan.txt") == "summarizer:plan"
    assert _langfuse_name_for(Path("/tmp/random.txt")) is None
