"""Tests for llm_common.context — ContextVar propagation."""

import pytest

from llm_common.context import (
    USAGE_ID_VARS,
    bind_usage_ids,
    llm_feature_var,
    llm_request_id_var,
    llm_run_id_var,
    llm_session_id_var,
    llm_user_id_var,
    llm_video_id_var,
    usage_ids,
    usage_scope,
)


def test_feature_var_default():
    assert llm_feature_var.get() == "unknown"


def test_feature_var_set_get():
    token = llm_feature_var.set("summarize:chapter")
    assert llm_feature_var.get() == "summarize:chapter"
    llm_feature_var.reset(token)
    assert llm_feature_var.get() == "unknown"


def test_request_id_var_default():
    assert llm_request_id_var.get() is None


def test_video_id_var_default():
    assert llm_video_id_var.get() is None


def test_video_id_var_set():
    token = llm_video_id_var.set("vid-123")
    assert llm_video_id_var.get() == "vid-123"
    llm_video_id_var.reset(token)


# ─── D9 usage ids ─────────────────────────────────────────────────────────

_RUN_IDS = {
    "run_id": "run-1",
    "trace_id": "a" * 32,
    "session_id": "chat-sess",
    "attempt": 2,
    "pipeline_version": "v10",
    "bypass": True,
}


def test_should_read_none_for_every_run_id_when_nothing_is_bound():
    assert {name: usage_ids()[name] for name in _RUN_IDS} == dict.fromkeys(_RUN_IDS)


def test_should_set_the_named_ids_and_return_reset_tokens_when_binding():
    tokens = bind_usage_ids(**_RUN_IDS)
    try:
        assert {name: usage_ids()[name] for name in _RUN_IDS} == _RUN_IDS
    finally:
        for var, token in zip([USAGE_ID_VARS[name] for name in _RUN_IDS], tokens, strict=True):
            var.reset(token)
    assert llm_run_id_var.get() is None


def test_should_refuse_an_unknown_name_when_binding():
    with pytest.raises(TypeError, match="unknown usage id"):
        bind_usage_ids(runid="typo")


def test_should_clear_the_ids_a_scope_was_not_given_when_it_opens():
    token = llm_user_id_var.set("previous-job-user")
    try:
        with usage_scope(run_id="run-2"):
            assert llm_run_id_var.get() == "run-2"
            assert llm_user_id_var.get() is None
        assert llm_user_id_var.get() == "previous-job-user"
    finally:
        llm_user_id_var.reset(token)


def test_should_restore_ids_set_inside_a_scope_when_it_closes():
    """The worker inheritance bug: a job's own sets must not survive the job."""
    with usage_scope(run_id="run-3"):
        bind_usage_ids(session_id="set-mid-job", run_id="replaced")
    assert llm_session_id_var.get() is None
    assert llm_run_id_var.get() is None


def test_should_restore_the_ids_when_the_job_fails():
    with pytest.raises(RuntimeError), usage_scope(run_id="run-4"):
        raise RuntimeError("job failed")
    assert llm_run_id_var.get() is None
