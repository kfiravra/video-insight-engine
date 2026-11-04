"""The admin's vendored copies of llm-common code stay identical to their source (plan A3, P-13).

vie-admin does not install llm-common, so it carries two copies: the Sentry
``before_send`` scrub (the marked block of ``llm_common/sentry_init.py``) and
the structlog → stdlib bridge (``llm_common/logging_setup.py``). A change to
one side without the other fails here. The sources are read as files from the
repo, never imported.
"""

from __future__ import annotations

from pathlib import Path

import pytest

ADMIN_SRC = Path(__file__).resolve().parents[1] / "src"
LLM_COMMON = Path(__file__).resolve().parents[3] / "packages" / "llm-common" / "src" / "llm_common"
BEGIN = "# ─── scrub: BEGIN"
END = "# ─── scrub: END ───"


def _require(path: Path) -> str:
    if not path.exists():
        pytest.skip(f"{path} is not in this checkout (container run)")
    return path.read_text(encoding="utf-8")


def _scrub_block(text: str) -> str:
    return text[text.index(BEGIN) : text.index(END) + len(END)]


def test_should_vendor_the_sentry_scrub_verbatim():
    source = _require(LLM_COMMON / "sentry_init.py")
    vendored = (ADMIN_SRC / "sentry_scrub.py").read_text(encoding="utf-8")

    assert _scrub_block(vendored) == _scrub_block(source)


def test_should_vendor_the_logging_bridge_verbatim():
    source = _require(LLM_COMMON / "logging_setup.py")
    vendored = (ADMIN_SRC / "logging_bridge.py").read_text(encoding="utf-8")

    # The copy only adds a leading comment block naming its source.
    assert vendored.endswith(source)
    assert all(line.startswith("#") for line in vendored[: -len(source)].splitlines())
