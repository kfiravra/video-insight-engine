"""scripts/register_prompts.py: lookup failures and a missing SDK must surface.

A dry run used to report every lookup failure (bad keys / 401, network) as
``would-upload`` and exit 0; the keys-set + SDK-missing branch was untested.
"""

from __future__ import annotations

import importlib.util
import logging
import sys
from pathlib import Path
from types import ModuleType
from unittest.mock import MagicMock

import pytest

_SCRIPT = Path(__file__).resolve().parents[3] / "scripts" / "register_prompts.py"


def _load_script() -> ModuleType:
    spec = importlib.util.spec_from_file_location("register_prompts_sync_test", _SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules["register_prompts_sync_test"] = module
    spec.loader.exec_module(module)
    return module


class _ApiError(Exception):
    def __init__(self, status_code: int) -> None:
        super().__init__(f"HTTP {status_code}")
        self.status_code = status_code


class _Registry:
    """get_prompt raises ``error`` when set, else serves ``text``."""

    def __init__(self, *, error: Exception | None = None, text: str = "SAME") -> None:
        self.error = error
        self.text = text
        self.creates: list[str] = []

    def get_prompt(self, name: str, *, label: str | None = None) -> object:  # noqa: ARG002
        if self.error is not None:
            raise self.error
        return MagicMock(prompt=self.text)

    def create_prompt(self, *, name: str, prompt: str, labels: list[str]) -> object:  # noqa: ARG002
        self.creates.append(name)
        return MagicMock()


def _record(mod: ModuleType, content: str = "NEW") -> object:
    return mod.PromptRecord(name="summarizer:plan", content=content, source_path=Path("plan.txt"))


def _run_main(
    monkeypatch: pytest.MonkeyPatch, mod: ModuleType, registry: object, argv: list[str]
) -> int:
    record = _record(mod)
    monkeypatch.setattr(mod, "discover_prompts", lambda: [record])
    monkeypatch.setattr(mod, "_build_client", lambda *, dry_run: registry)  # noqa: ARG005
    return mod.main(argv)


class TestLookupFailure:
    @pytest.mark.parametrize("error", [_ApiError(401), _ApiError(503), ConnectionError("down")])
    def test_should_report_unknown_when_dry_run_lookup_fails_for_a_reason_other_than_404(
        self, error: Exception
    ) -> None:
        mod = _load_script()

        result = mod.upload_prompt(_Registry(error=error), _record(mod), dry_run=True)

        assert result == "unknown"

    def test_should_report_would_upload_when_the_label_is_not_there_yet(self) -> None:
        mod = _load_script()

        result = mod.upload_prompt(_Registry(error=_ApiError(404)), _record(mod), dry_run=True)

        assert result == "would-upload"

    def test_should_exit_non_zero_when_a_dry_run_lookup_fails(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        mod = _load_script()

        assert _run_main(monkeypatch, mod, _Registry(error=_ApiError(401)), ["--dry-run"]) == 1

    def test_should_exit_zero_when_every_prompt_is_checked(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        mod = _load_script()

        assert _run_main(monkeypatch, mod, _Registry(text="OLD"), ["--dry-run"]) == 0

    def test_should_exit_non_zero_when_an_upload_fails(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        mod = _load_script()
        registry = _Registry(text="OLD")
        registry.create_prompt = MagicMock(side_effect=_ApiError(500))  # type: ignore[method-assign]

        assert _run_main(monkeypatch, mod, registry, ["--commit"]) == 1


class TestMissingSdk:
    @pytest.fixture(autouse=True)
    def _keys_set_sdk_missing(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("LANGFUSE_PUBLIC_KEY", "pk-test")
        monkeypatch.setenv("LANGFUSE_SECRET_KEY", "sk-test")
        # A None entry makes `from langfuse import Langfuse` raise ImportError.
        monkeypatch.setitem(sys.modules, "langfuse", None)

    def test_should_compare_nothing_when_dry_run_has_keys_but_no_sdk(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        mod = _load_script()

        with caplog.at_level(logging.WARNING, logger="register_prompts"):
            client = mod._build_client(dry_run=True)

        assert (client, "SDK is not installed" in caplog.text) == (None, True)

    def test_should_exit_when_commit_has_keys_but_no_sdk(self) -> None:
        mod = _load_script()

        with pytest.raises(SystemExit, match="Langfuse SDK is not installed"):
            mod._build_client(dry_run=False)
