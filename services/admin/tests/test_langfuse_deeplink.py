"""Langfuse deep links from stored trace ids (P-6): no Langfuse call; needs LANGFUSE_PROJECT_ID."""

from __future__ import annotations

import pytest

from src.config import settings
from src.services import langfuse


@pytest.fixture
def project(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "LANGFUSE_BASE_URL", "https://cloud.langfuse.com/")
    monkeypatch.setattr(settings, "LANGFUSE_PROJECT_ID", "proj-9")


def test_is_enabled_should_be_false_when_the_project_id_is_unset(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(settings, "LANGFUSE_PROJECT_ID", "")
    assert langfuse.is_enabled() is False


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


def test_latest_trace_ids_should_map_each_request_to_its_newest_attempt_when_retried():
    runs = [
        {"runId": "a", "requestId": "req-1", "traceId": "t-a"},
        {"runId": "b", "requestId": "req-2", "traceId": "t-b"},
        {"runId": "c", "requestId": "req-1", "traceId": "t-c"},
    ]
    assert langfuse.latest_trace_ids_by_request(runs) == {"req-1": "t-c", "req-2": "t-b"}


@pytest.mark.parametrize(
    "runs",
    [None, [], [{"runId": "a", "traceId": "t"}], [{"requestId": "r"}], ["junk"]],
)
def test_latest_trace_ids_should_skip_entries_when_an_id_is_missing(runs: object):
    assert langfuse.latest_trace_ids_by_request(runs) == {}  # type: ignore[arg-type]
