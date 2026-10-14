"""Tests for the faithfulness judge.

Covers:
- claim flattening from a nested extraction blob
- deterministic sampling by youtube_id
- judge response parsing (json + json-in-prose + garbage)
- end-to-end run with mocked LLM, including:
    * grounded vs ungrounded verdicts
    * judge errors don't crash the check
    * disabled when sample rate is 0
    * report is logged onto Langfuse trace via log_score
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from litellm import ModelResponse

from src.services.llm import LLMService
from src.services.llm_provider import LLMProvider
from src.services.observability import langfuse_client as lc
from src.services.pipeline import faithfulness as fh


@pytest.fixture(autouse=True)
def _reset_observability(monkeypatch):
    lc._reset_for_tests()
    monkeypatch.setattr(lc.settings, "LANGFUSE_PUBLIC_KEY", None, raising=False)
    monkeypatch.setattr(lc.settings, "LANGFUSE_SECRET_KEY", None, raising=False)
    yield
    lc._reset_for_tests()


# ─── Claim flattening ───────────────────────────────────────────────────
def test_flatten_claims_picks_known_leaf_fields():
    """Claims under 20 chars are filtered as boilerplate; longer leaves are kept."""
    data = {
        "concepts": [
            {
                "name": "Photosynthesis basics 101",  # > 20 chars so it survives
                "definition": "the process by which plants make food from sunlight",
            },
        ],
        "key_points": [
            {"text": "chlorophyll absorbs red and blue light, reflects green"},
        ],
        "noise": {"randomKey": "irrelevant"},
    }
    claims = fh._flatten_claims(data)
    assert any("Photosynthesis basics" in c for c in claims)
    assert any("chlorophyll" in c for c in claims)
    # Boilerplate / unknown keys aren't pulled in
    assert not any("randomKey" in c for c in claims)


def test_flatten_claims_filters_short_text():
    data = {
        "key_points": [{"text": "ok"}, {"text": "long enough to be a real claim about something"}]
    }
    claims = fh._flatten_claims(data)
    assert len(claims) == 1
    assert "long enough" in claims[0]


def test_flatten_claims_deduplicates():
    data = {
        "a": [{"text": "same claim about a thing here repeated"}],
        "b": {"text": "same claim about a thing here repeated"},
    }
    claims = fh._flatten_claims(data)
    assert len(claims) == 1


# ─── Sampling ───────────────────────────────────────────────────────────
def test_sample_claims_deterministic_per_video():
    claims = [f"claim number {i} something happened here that is long enough" for i in range(20)]
    first = fh._sample_claims(claims, rate=0.2, youtube_id="abc123")
    second = fh._sample_claims(claims, rate=0.2, youtube_id="abc123")
    assert first == second


def test_sample_claims_rate_zero_returns_empty():
    assert fh._sample_claims(["a"], rate=0.0, youtube_id="x") == []


def test_sample_claims_caps_at_six():
    claims = [f"claim {i} something here long enough" for i in range(100)]
    sample = fh._sample_claims(claims, rate=1.0, youtube_id="x")
    assert len(sample) == fh._MAX_SAMPLES_PER_VIDEO


# ─── Judge response parsing ─────────────────────────────────────────────
def test_parse_grounded_true():
    assert fh._parse_judge_response('{"grounded": true, "evidence": "x"}') is True


def test_parse_grounded_false():
    assert fh._parse_judge_response('{"grounded": false, "evidence": ""}') is False


def test_parse_handles_prose_wrap():
    raw = 'Here is my verdict:\n{"grounded": true, "evidence": "y"}\nThanks!'
    assert fh._parse_judge_response(raw) is True


def test_parse_handles_string_boolean():
    assert fh._parse_judge_response('{"grounded": "true"}') is True
    assert fh._parse_judge_response('{"grounded": "false"}') is False


def test_parse_garbage_returns_none():
    assert fh._parse_judge_response("nope") is None
    assert fh._parse_judge_response("") is None
    assert fh._parse_judge_response('{"other": 1}') is None


# ─── End-to-end ─────────────────────────────────────────────────────────
def _llm_returning(verdicts: list[str]):
    """Return a MagicMock LLMService that yields the given judge outputs."""
    service = MagicMock()
    service.call_llm = AsyncMock(side_effect=verdicts)
    service.call_llm_fast = AsyncMock(side_effect=verdicts)
    service.model = "anthropic/claude-sonnet-4-6"
    service.fast_model = "anthropic/claude-haiku-4-5-20251001"
    return service


@pytest.mark.asyncio
async def test_disabled_when_sample_rate_zero():
    llm = _llm_returning([])
    report = await fh.run_faithfulness_check(
        llm_service=llm,
        transcript="t",
        extraction_data={"key_points": [{"text": "some long claim worth scoring here"}]},
        youtube_id="vid",
        sample_rate=0.0,
    )
    assert report is None
    llm.call_llm_fast.assert_not_called()


@pytest.mark.asyncio
async def test_returns_none_when_no_claims():
    llm = _llm_returning([])
    report = await fh.run_faithfulness_check(
        llm_service=llm,
        transcript="t",
        extraction_data={"key_points": []},
        youtube_id="vid",
        sample_rate=0.2,
    )
    assert report is None


@pytest.mark.asyncio
async def test_reports_grounded_score_and_logs_to_langfuse(monkeypatch):
    # Install fake Langfuse so log_score has a trace target
    fake_sdk = MagicMock()
    monkeypatch.setattr(lc, "Langfuse", fake_sdk)
    monkeypatch.setattr(lc.settings, "LANGFUSE_PUBLIC_KEY", "pk", raising=False)
    monkeypatch.setattr(lc.settings, "LANGFUSE_SECRET_KEY", "sk", raising=False)
    lc.init_langfuse()
    fake_trace = MagicMock()
    lc._client.trace.return_value = fake_trace

    llm = _llm_returning(
        [
            '{"grounded": true, "evidence": ""}',
            '{"grounded": false, "evidence": ""}',
            '{"grounded": true, "evidence": ""}',
        ]
    )
    extraction = {
        "key_points": [
            {"text": "claim about photosynthesis being the process plants use"},
            {"text": "claim about light reactions occurring in thylakoid membranes"},
            {"text": "claim about calvin cycle producing glucose in stroma"},
        ],
    }

    async with lc.pipeline_trace("vid"):
        report = await fh.run_faithfulness_check(
            llm_service=llm,
            transcript="photosynthesis happens. light reactions occur. calvin cycle.",
            extraction_data=extraction,
            youtube_id="vid",
            sample_rate=1.0,
        )

    assert report is not None
    assert report.sample_size == 3
    assert report.grounded_count == 2
    assert report.score == pytest.approx(0.667, abs=0.01)
    fake_trace.score.assert_called_once()
    assert fake_trace.score.call_args.kwargs["name"] == "faithfulness"


@pytest.mark.asyncio
async def test_judge_errors_dont_crash():
    """When the judge returns garbage, we just skip that verdict."""
    llm = _llm_returning(["garbage", '{"grounded": true}', '{"grounded": true}'])
    extraction = {
        "key_points": [
            {"text": f"claim {i} about something with enough length to count"} for i in range(3)
        ]
    }
    report = await fh.run_faithfulness_check(
        llm_service=llm,
        transcript="t",
        extraction_data=extraction,
        youtube_id="vid",
        sample_rate=1.0,
    )
    # 3 sampled, 1 garbage → 2 usable, 2 grounded → score 1.0
    assert report is not None
    assert report.sample_size == 2
    assert report.score == 1.0


@pytest.mark.asyncio
async def test_transcript_window_covers_long_videos():
    """Regression: an 80K-char transcript (≈ 80-min video) must reach the judge.

    Previously the budget was 12K chars, which truncated everything past
    the opening 12 minutes — claims from later chapters were never in-window
    and the judge always returned `false`, dragging the score to 0/N.
    """
    assert fh._TRANSCRIPT_BUDGET_CHARS >= 60_000


# ─── Claim-localized context selection ──────────────────────────────────
_FILLER_SENTENCE = (
    "the presenter keeps talking about general background material and "
    "various unrelated housekeeping remarks for quite a while here. "
)
_TAIL_EVIDENCE = (
    "the quantum flux capacitor requires exactly three plutonium rods "
    "to reach eighty eight miles per hour according to doc brown"
)


def _long_transcript_with_tail_evidence() -> str:
    """~120K chars of filler with the supporting evidence past the 80K mark."""
    filler = _FILLER_SENTENCE * 900  # ~100K chars
    assert len(filler) > fh._TRANSCRIPT_BUDGET_CHARS
    return filler + _TAIL_EVIDENCE + " " + _FILLER_SENTENCE * 150


class TestSelectClaimContext:
    def test_short_transcript_passes_through_whole(self):
        transcript = "a short transcript about photosynthesis and chlorophyll"
        result = fh._select_claim_context(transcript, "claim about chlorophyll")
        assert result == transcript

    def test_empty_transcript_returns_empty(self):
        assert fh._select_claim_context("", "any claim at all") == ""

    def test_includes_supporting_segment_beyond_head_budget(self):
        """The tail evidence lives past the first 80K chars — a head slice
        could never contain it; claim-localized selection must."""
        transcript = _long_transcript_with_tail_evidence()
        claim = "The flux capacitor needs three plutonium rods to hit 88 mph"

        head_slice = transcript[: fh._TRANSCRIPT_BUDGET_CHARS]
        assert _TAIL_EVIDENCE not in head_slice  # old behavior judged against this

        context = fh._select_claim_context(transcript, claim)
        assert _TAIL_EVIDENCE in context

    def test_respects_budget(self):
        transcript = _long_transcript_with_tail_evidence()
        claim = "The flux capacitor needs three plutonium rods"
        context = fh._select_claim_context(transcript, claim)
        # Budget + join/gap-marker slack
        assert len(context) <= fh._TRANSCRIPT_BUDGET_CHARS + 2_048

    def test_no_overlap_falls_back_to_head_slice(self):
        transcript = _FILLER_SENTENCE * 900
        claim = "zzzxqwv jjkkyy uuvvww"  # shares no vocabulary
        context = fh._select_claim_context(transcript, claim)
        assert context.startswith(transcript[:200])
        assert len(context) <= fh._TRANSCRIPT_BUDGET_CHARS + len("\n[truncated]")

    def test_stopword_only_claim_falls_back_to_head_slice(self):
        transcript = _FILLER_SENTENCE * 900
        context = fh._select_claim_context(transcript, "the and for that this")
        assert context.startswith(transcript[:200])

    def test_selected_windows_preserve_document_order(self):
        early = "alpha wombat discusses the migration pattern of wombats early on "
        late = "and much later the wombat migration pattern conclusion is revealed "
        transcript = early * 80 + _FILLER_SENTENCE * 900 + late * 80
        context = fh._select_claim_context(transcript, "wombat migration pattern")
        first_early = context.find("alpha wombat")
        first_late = context.find("later the wombat")
        assert first_early != -1 and first_late != -1
        assert first_early < first_late  # document order preserved

    def test_split_windows_covers_whole_transcript_in_order(self):
        transcript = "word " * 5000  # 25K chars
        windows = fh._split_windows(transcript, window_chars=4_000)
        assert [w.index for w in windows] == list(range(len(windows)))
        assert windows[0].offset == 0
        reassembled = "".join(w.text for w in windows)
        assert reassembled == transcript

    def test_split_windows_empty(self):
        assert fh._split_windows("") == []


@pytest.mark.asyncio
async def test_judge_receives_claim_localized_context_for_long_video():
    """End-to-end: for a 3h-style transcript the prompt sent to the judge
    contains the tail evidence, not just the transcript head."""
    transcript = _long_transcript_with_tail_evidence()
    claim_text = (
        "the quantum flux capacitor requires exactly three plutonium rods "
        "to reach eighty eight miles per hour"
    )
    llm = _llm_returning(['{"grounded": true, "evidence": "flux capacitor"}'])
    extraction = {"key_points": [{"text": claim_text}]}

    report = await fh.run_faithfulness_check(
        llm_service=llm,
        transcript=transcript,
        extraction_data=extraction,
        youtube_id="vid-long",
        sample_rate=1.0,
    )

    assert report is not None
    prompt_sent = llm.call_llm_fast.call_args.args[0]
    assert _TAIL_EVIDENCE in prompt_sent


@pytest.mark.asyncio
async def test_run_logs_diagnostic_summary(caplog):
    """Every run should emit a one-line summary with transcript size + sample size."""
    llm = _llm_returning(['{"grounded": true}', '{"grounded": true}'])
    extraction = {
        "key_points": [
            {"text": "claim one with enough characters to be considered"},
            {"text": "claim two with enough characters to be considered"},
        ]
    }
    with caplog.at_level("INFO"):
        await fh.run_faithfulness_check(
            llm_service=llm,
            transcript="t" * 5000,
            extraction_data=extraction,
            youtube_id="vid-log",
            sample_rate=1.0,
        )
    summary = [
        r
        for r in caplog.records
        if "[faithfulness]" in r.getMessage() and "claims_total" in r.getMessage()
    ]
    assert summary, "expected one diagnostic summary log line per run"
    assert "vid-log" in summary[0].getMessage()
    assert "truncated=False" in summary[0].getMessage()


@pytest.mark.asyncio
async def test_judge_exception_returns_none_verdict():
    """An exception inside call_llm_with_retry is logged and treated as None."""
    llm = MagicMock()
    llm.call_llm_fast = AsyncMock(side_effect=RuntimeError("boom"))
    llm.call_llm = AsyncMock(side_effect=RuntimeError("boom"))
    llm.model = "anthropic/claude-sonnet-4-6"
    llm.fast_model = "anthropic/claude-haiku-4-5-20251001"
    extraction = {"key_points": [{"text": "claim long enough to be useful here"}]}
    report = await fh.run_faithfulness_check(
        llm_service=llm,
        transcript="t",
        extraction_data=extraction,
        youtube_id="vid",
        sample_rate=1.0,
    )
    # Single claim, judge errored → no usable verdicts → returns None
    assert report is None


# ─── Judge model (LLM_JUDGE_MODEL) ──────────────────────────────────────
_LUNA = "openai/gpt-6-luna"
_MINI = "openai/gpt-4o-mini"


def _verdict_response() -> ModelResponse:
    message = {"role": "assistant", "content": '{"grounded": true, "evidence": "it says so"}'}
    return ModelResponse(choices=[{"index": 0, "finish_reason": "stop", "message": message}])


async def _judge_request(llm_service: LLMService) -> tuple[bool | None, dict[str, Any]]:
    """Judge one claim through the real retry wrapper + provider; return the verdict + kwargs sent."""
    sent = AsyncMock(return_value=_verdict_response())
    with patch("src.services.llm_provider.acompletion", sent):
        verdict = await fh._judge_one(llm_service, "the transcript says so", "a claim to judge")
    return verdict, sent.call_args.kwargs


class TestJudgeModel:
    @pytest.mark.asyncio
    async def test_should_send_the_judge_to_llm_judge_model_with_reasoning_off_when_it_is_set(
        self, monkeypatch
    ):
        monkeypatch.setattr(fh.settings, "LLM_JUDGE_MODEL", _LUNA)
        fast_tier = LLMService(LLMProvider(model=_MINI, fast_model=_MINI))

        verdict, kwargs = await _judge_request(fast_tier)

        assert (verdict, kwargs["model"], kwargs.get("reasoning_effort")) == (True, _LUNA, "none")

    @pytest.mark.asyncio
    async def test_should_keep_the_judge_on_the_fast_tier_when_the_setting_is_blank(self):
        # conftest blanks every stage setting, LLM_JUDGE_MODEL included: the rollback.
        fast_tier = LLMService(LLMProvider(model="anthropic/claude-sonnet-5-5", fast_model=_MINI))

        verdict, kwargs = await _judge_request(fast_tier)

        assert (verdict, kwargs["model"], "reasoning_effort" in kwargs) == (True, _MINI, False)
