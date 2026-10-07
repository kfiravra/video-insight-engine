"""Registry/disk placeholder guard in the registry-first prompt loader.

A registry version whose ``{placeholder}`` set differs from the shipped
``.txt`` was written for other code — the loader must render the shipped file
(and warn once) so deploying code and registering prompts are safe in either
order. Wording-only registry edits keep the same slots and still win.
No Langfuse calls: the registry fetch is patched.
"""

from __future__ import annotations

import logging
from collections.abc import Iterator
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from src import config as app_config
from src.services.observability import langfuse_client as lc
from src.services.pipeline import prompt_builder, prompt_registry
from src.services.pipeline.prompt_builder import (
    PROMPTS_DIR,
    load_prompt_text,
    load_prompt_with_fallback,
)
from src.services.pipeline.prompt_registry import declared_placeholders

DISK_TEXT = 'Title: {title}\nTranscript:\n{transcript}\nReturn {{"ok": true}}'
_DRIFT_EVENT = "prompt_registry.placeholder_drift"


class _RegistryPrompt:
    """Minimal stand-in for a Langfuse Prompt object."""

    def __init__(self, text: str) -> None:
        self.prompt = text
        self.version = 4


@pytest.fixture(autouse=True)
def _registry_mode(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    monkeypatch.setattr(app_config.settings, "PROMPT_SOURCE", "registry")
    monkeypatch.setattr(prompt_registry, "_DRIFT_WARNED", set())
    lc._reset_for_tests()
    yield
    lc._reset_for_tests()


@pytest.fixture
def disk_prompt(tmp_path: Path) -> Path:
    path = tmp_path / "plan.txt"
    path.write_text(DISK_TEXT)
    return path


def _load(
    path: Path, registry_text: str | None, record: MagicMock, name: str = "summarizer:plan"
) -> str:
    fetched = None if registry_text is None else _RegistryPrompt(registry_text)
    with (
        patch("src.services.observability.fetch_prompt_with_obj", return_value=fetched),
        patch("src.services.observability.record_active_prompt", record),
    ):
        return load_prompt_with_fallback(langfuse_name=name, fallback_path=path)


def _drift_records(caplog: pytest.LogCaptureFixture) -> list[logging.LogRecord]:
    return [r for r in caplog.records if r.getMessage().startswith(_DRIFT_EVENT)]


class TestDeclaredPlaceholders:
    def test_should_return_slot_names_when_text_has_placeholders(self) -> None:
        assert declared_placeholders("{title} and {transcript}") == {"title", "transcript"}

    def test_should_not_count_double_brace_escapes_as_placeholders(self) -> None:
        text = 'Return {{"domain": "tech"}} or {{literal}} for {title}'

        assert declared_placeholders(text) == {"title"}

    def test_should_count_a_slot_wrapped_in_escaped_braces(self) -> None:
        assert declared_placeholders("{{{title}}}") == {"title"}


class TestRegistryPlaceholderDrift:
    def test_should_use_disk_text_when_registry_placeholders_differ(
        self, disk_prompt: Path
    ) -> None:
        stale = "Title: {title}\nPreview: {transcript_preview}"

        assert _load(disk_prompt, stale, MagicMock()) == DISK_TEXT

    def test_should_warn_once_when_drifted_prompt_loads_repeatedly(
        self, disk_prompt: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        stale = "Title: {title}\nPreview: {transcript_preview}"
        with caplog.at_level(logging.WARNING):
            for _ in range(3):
                _load(disk_prompt, stale, MagicMock())

        assert len(_drift_records(caplog)) == 1

    def test_should_name_prompt_and_differing_slots_without_content_when_warning(
        self, disk_prompt: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        stale = "SECRET WORDING {title} {transcript_preview}"
        with caplog.at_level(logging.WARNING):
            _load(disk_prompt, stale, MagicMock())

        (record,) = _drift_records(caplog)
        assert (
            record.__dict__["prompt_name"],
            record.__dict__["missing_in_registry"],
            record.__dict__["extra_in_registry"],
            "SECRET WORDING" in record.getMessage(),
        ) == ("summarizer:plan", ["transcript"], ["transcript_preview"], False)

    def test_should_warn_per_prompt_name_when_several_prompts_drift(
        self, disk_prompt: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        with caplog.at_level(logging.WARNING):
            _load(disk_prompt, "{title}", MagicMock(), name="summarizer:plan")
            _load(disk_prompt, "{title}", MagicMock(), name="summarizer:memory")

        assert [r.__dict__["prompt_name"] for r in _drift_records(caplog)] == [
            "summarizer:plan",
            "summarizer:memory",
        ]

    def test_should_not_link_registry_version_when_disk_text_is_used(
        self, disk_prompt: Path
    ) -> None:
        record = MagicMock()

        _load(disk_prompt, "{title} {transcript_preview}", record)

        record.assert_not_called()

    def test_should_use_disk_plan_when_registry_holds_pre_rewrite_plan(self) -> None:
        plan_path = PROMPTS_DIR / "plan.txt"
        shipped = prompt_builder._read_file_cached(str(plan_path))
        pre_rewrite = shipped + "\n{transcript_preview}{content_traits}"
        with (
            patch(
                "src.services.observability.fetch_prompt_with_obj",
                return_value=_RegistryPrompt(pre_rewrite),
            ),
            patch("src.services.observability.record_active_prompt"),
        ):
            assert load_prompt_text(plan_path) == shipped


class TestRegistryPlaceholdersMatch:
    def test_should_use_registry_text_when_only_wording_differs(self, disk_prompt: Path) -> None:
        reworded = "Video title is {title}.\nFull transcript follows:\n{transcript}"

        assert _load(disk_prompt, reworded, MagicMock()) == reworded

    def test_should_link_registry_version_when_registry_text_is_used(
        self, disk_prompt: Path
    ) -> None:
        record = MagicMock()

        _load(disk_prompt, DISK_TEXT, record)

        record.assert_called_once()

    def test_should_use_registry_text_when_it_adds_only_escaped_braces(
        self, disk_prompt: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        with_example = DISK_TEXT + '\nExample: {{"steps": [{{"label": "x"}}]}}'
        with caplog.at_level(logging.WARNING):
            out = _load(disk_prompt, with_example, MagicMock())

        assert (out, _drift_records(caplog)) == (with_example, [])

    def test_should_use_registry_text_when_local_file_is_missing(self, tmp_path: Path) -> None:
        registry_text = "{anything}"

        assert _load(tmp_path / "gone.txt", registry_text, MagicMock()) == registry_text


class TestUnchangedPaths:
    def test_should_use_disk_text_when_prompt_is_not_registered(self, disk_prompt: Path) -> None:
        assert _load(disk_prompt, None, MagicMock()) == DISK_TEXT

    def test_should_skip_registry_and_guard_when_prompt_source_is_disk(
        self, monkeypatch: pytest.MonkeyPatch, disk_prompt: Path
    ) -> None:
        monkeypatch.setattr(app_config.settings, "PROMPT_SOURCE", "disk")
        monkeypatch.setattr(app_config.settings, "ENVIRONMENT", "development")
        fetch = MagicMock(return_value=_RegistryPrompt("{title} {transcript_preview}"))
        with patch("src.services.observability.fetch_prompt_with_obj", fetch):
            out = load_prompt_with_fallback(
                langfuse_name="summarizer:plan", fallback_path=disk_prompt
            )

        assert (out, fetch.called) == (DISK_TEXT, False)
