"""Langfuse deep links from stored trace ids (P-6): no Langfuse call; needs LANGFUSE_PROJECT_ID."""

from __future__ import annotations

import pytest

from src.config import settings
from src.services import langfuse


@pytest.fixture
def project(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "LANGFUSE_BASE_URL", "https://cloud.langfuse.com/")
    monkeypatch.setattr(settings, "LANGFUSE_PROJECT_ID", "proj-9")


def test_trace_url_should_build_the_trace_page_when_the_project_is_set(project: None):
    assert (
        langfuse.trace_url("ab" * 16)
        == f"https://cloud.langfuse.com/project/proj-9/traces/{'ab' * 16}"
    )


def test_session_url_should_build_the_session_page_when_the_project_is_set(project: None):
    assert langfuse.session_url("pipeline-v1") == (
        "https://cloud.langfuse.com/project/proj-9/sessions/pipeline-v1"
    )


@pytest.mark.parametrize("trace_id", [None, ""])
def test_trace_url_should_be_none_when_there_is_no_trace_id(project: None, trace_id: str | None):
    assert langfuse.trace_url(trace_id) is None


def test_urls_should_be_none_when_the_project_id_is_unset(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(settings, "LANGFUSE_PROJECT_ID", "")
    assert (langfuse.trace_url("t"), langfuse.session_url("s")) == (None, None)


def test_run_entry_should_pick_the_attempt_with_the_run_id_when_the_row_has_one():
    runs = [
        {"runId": "a", "requestId": "req-1", "traceId": "t-a"},
        {"runId": "b", "requestId": "req-1", "traceId": "t-b"},
    ]
    assert langfuse.run_entry(runs, run_id="a", request_id="req-1") == runs[0]


def test_run_entry_should_pick_the_newest_attempt_of_the_request_when_a_legacy_row_has_no_run_id():
    runs = [
        {"runId": "a", "requestId": "req-1", "traceId": "t-a"},
        {"runId": "b", "requestId": "req-2", "traceId": "t-b"},
        {"runId": "c", "requestId": "req-1", "traceId": "t-c"},
    ]
    assert langfuse.run_entry(runs, run_id=None, request_id="req-1") == runs[2]


@pytest.mark.parametrize(
    "runs",
    [None, [], ["junk"], [{"runId": "other", "requestId": "req-1"}]],
)
def test_run_entry_should_be_none_when_no_entry_matches(runs: object):
    assert langfuse.run_entry(runs, run_id="a", request_id=None) is None  # type: ignore[arg-type]


def test_run_entry_should_be_none_when_neither_id_is_known():
    assert (
        langfuse.run_entry([{"runId": "a", "requestId": "r"}], run_id=None, request_id=None) is None
    )
