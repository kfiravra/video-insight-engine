"""Tests for llm_common.context — ContextVar propagation."""

from llm_common.context import llm_feature_var, llm_request_id_var, llm_video_id_var


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
import pytest  # noqa: E402

from llm_common.context import (  # noqa: E402
    USAGE_ID_VARS,
    bind_usage_ids,
    llm_run_id_var,
    llm_session_id_var,
    llm_user_id_var,
    usage_ids,
    usage_scope,
)

_RUN_IDS = {
    "run_id": "run-1",
    "trace_id": "a" * 32,
    "session_id": "chat-sess",
    "attempt": 2,
    "pipeline_version": "v10",
    "bypass": True,
}


def test_new_usage_ids_default_to_none():
    assert {name: usage_ids()[name] for name in _RUN_IDS} == dict.fromkeys(_RUN_IDS)


def test_bind_usage_ids_sets_named_ids_and_returns_reset_tokens():
    tokens = bind_usage_ids(**_RUN_IDS)
    try:
        assert {name: usage_ids()[name] for name in _RUN_IDS} == _RUN_IDS
    finally:
        for var, token in zip([USAGE_ID_VARS[name] for name in _RUN_IDS], tokens, strict=True):
            var.reset(token)
    assert llm_run_id_var.get() is None


def test_bind_usage_ids_rejects_an_unknown_name():
    with pytest.raises(TypeError, match="unknown usage id"):
        bind_usage_ids(runid="typo")


def test_usage_scope_clears_ids_it_was_not_given():
    token = llm_user_id_var.set("previous-job-user")
    try:
        with usage_scope(run_id="run-2"):
            assert llm_run_id_var.get() == "run-2"
            assert llm_user_id_var.get() is None
        assert llm_user_id_var.get() == "previous-job-user"
    finally:
        llm_user_id_var.reset(token)


def test_usage_scope_restores_ids_set_inside_it():
    """The worker inheritance bug: a job's own sets must not survive the job."""
    with usage_scope(run_id="run-3"):
        bind_usage_ids(session_id="set-mid-job", run_id="replaced")
    assert llm_session_id_var.get() is None
    assert llm_run_id_var.get() is None


def test_usage_scope_restores_on_error():
    with pytest.raises(RuntimeError), usage_scope(run_id="run-4"):
        raise RuntimeError("job failed")
    assert llm_run_id_var.get() is None
