"""Tests for ``llm_common.observability.ids``: run trace ids and the user-id policy."""

from __future__ import annotations

import hashlib

import pytest
from langfuse import Langfuse

from llm_common.observability.ids import resolve_user_id, trace_id_for_run

# One pinned vector: the api computes the same id in TypeScript (P0b).
RUN_ID = "6f1c2d3e-4a5b-4c6d-8e7f-001122334455"
RUN_TRACE_ID = hashlib.sha256(RUN_ID.encode()).hexdigest()[:32]


def test_trace_id_for_run_should_be_the_truncated_sha256_when_given_a_run_id():
    assert trace_id_for_run(RUN_ID) == RUN_TRACE_ID
    assert len(RUN_TRACE_ID) == 32


def test_trace_id_for_run_should_match_the_sdk_when_seeded_with_the_run_id():
    assert trace_id_for_run(RUN_ID) == Langfuse.create_trace_id(seed=RUN_ID)


def test_resolve_user_id_should_pass_through_when_the_mode_is_identity():
    assert resolve_user_id("identity", "", "u1") == "u1"
    assert resolve_user_id(None, None, "u1") == "u1"


def test_resolve_user_id_should_return_none_when_the_mode_is_omit():
    assert resolve_user_id("OMIT", "", "u1") is None


def test_resolve_user_id_should_hash_with_the_salt_when_the_mode_is_hash():
    expected = hashlib.sha256(b"pepper::u1").hexdigest()[:16]
    assert resolve_user_id("hash", "pepper", "u1") == expected
    assert resolve_user_id("hash", "other", "u1") != expected


@pytest.mark.parametrize("mode", ["identity", "hash", "omit"])
def test_resolve_user_id_should_return_none_when_there_is_no_user(mode):
    assert resolve_user_id(mode, "s", None) is None
    assert resolve_user_id(mode, "s", "") is None


def test_resolve_user_id_should_pass_through_when_the_mode_is_unknown():
    assert resolve_user_id("bogus", "", "u1") == "u1"
