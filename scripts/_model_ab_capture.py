"""Request capture for the model A/B (pipeline-1min 4.0d).

Two seams, both the provider's single ``acompletion`` call (as
``tmp/.../tools/capture.py`` and the replay harness use it):

* **Cassettes** — the real ``run_replay`` driver (speed 0, zero network, zero
  spend) runs each replay cassette in split mode with the faithfulness judge
  switched on; ``ReplayLLM.acompletion`` is wrapped so every request is
  recorded, with its feature/span/ordinal, before the fake answers it. Two
  more wrappers publish what the scorers need: the plan's normalisation
  inputs (``plan._plan_from_response``) and each extraction group's spec and
  active domains (``extraction_groups.run_group_call``).
* **Direct builders** — :func:`recording_provider` replaces ``acompletion``
  with a recorder that answers a canned reply, so the pipeline's own builders
  (tier probe, ``translate_flat_list``, the vision batches) emit their request
  exactly as they would to a provider.
"""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import AsyncIterator, Callable, Iterator
from contextlib import ExitStack, contextmanager
from contextvars import ContextVar
from dataclasses import asdict
from typing import Any
from unittest.mock import patch

from _model_ab_types import GPT_4O_MINI, HAIKU_45, SONNET_46, TODAY, AbRequest
from litellm import ModelResponse
from tests.replay import fake_llm
from tests.replay.cassette import load_cassette
from tests.replay.driver import run_replay

from llm_common.context import llm_feature_var
from src.services.pipeline import extraction_groups, plan
from src.services.pipeline.extraction_prompt import ExtractionPrompt
from src.services.pipeline.field_groups import ExtractionGroup
from src.services.pipeline.field_requests import FieldRequest
from src.services.pipeline.phases import split_extraction

logger = logging.getLogger(__name__)

CASSETTE_VIDEOS: tuple[str, ...] = ("T1dQhQAm8Tc", "jMq8lEu-of0", "uC45_4nnEAI")
# The judge is off in the harness (its sampled calls are not recorded); on
# here so its real requests are captured (the fake then misses, which the
# judge swallows — nothing else in the run changes).
_CAPTURE_SETTINGS: dict[str, Any] = {"LANGFUSE_FAITHFULNESS_SAMPLE_RATE": 1.0}
# Round 1's TODAY models, pinned: since bac879b the config defaults are the
# 4.0d picks (plan Sonnet 5.5 at effort medium …), and a request captured for a
# 5.5 model would carry its thinking / effort params. The cassettes record the
# same values; the pin keeps a capture from depending on them.
TODAY_SETTINGS: dict[str, Any] = {
    "LLM_MODEL": SONNET_46,
    "LLM_FAST_MODEL": GPT_4O_MINI,
    "LLM_CLASSIFIER_MODEL": HAIKU_45,
    "LLM_EXTRACTION_MODEL": HAIKU_45,
    "LLM_MEMORY_MODEL": HAIKU_45,
    "LLM_VISION_MODEL": SONNET_46,
    "LLM_ENRICHMENT_MODEL": GPT_4O_MINI,
    "LLM_DESCRIPTION_MODEL": None,
    "LLM_TRANSLATION_MODEL": None,
    # The judge on the fast tier (gpt-4o-mini), as round 1 captured it.
    "LLM_JUDGE_MODEL": None,
}
# Params only a model's request rules add — never part of a captured request.
_MODEL_PARAMS = frozenset({"thinking", "output_config", "reasoning_effort"})
_CAPTURE_OVERRIDES: dict[str, Any] = {**_CAPTURE_SETTINGS, **TODAY_SETTINGS}

_FEATURE_CALL_TYPES: dict[str, str] = {
    "summarize:metadata": "description",
    "summarize:tier_probe": "tier_probe",
    "summarize:plan": "plan",
    "summarize:memory": "memory",
    "summarize:enrichment": "quiz",
    "summarize:faithfulness": "faithfulness",
}
_EXTRACTION_PREFIX = "summarize:extraction:"
# Answer size for a request the cassette has no answer for (the judge: ≈ 30–60 tokens).
_UNRECORDED_OUTPUT_TOKENS: dict[str, int] = {"faithfulness": 60}

_group_ctx: ContextVar[dict[str, Any] | None] = ContextVar("model_ab_group", default=None)


def call_type_of(feature: str) -> str | None:
    """The A/B call type of a cassette feature; None for one the A/B does not send."""
    if feature.startswith(_EXTRACTION_PREFIX):
        return "extraction"
    return _FEATURE_CALL_TYPES.get(feature)


def plain(kwargs: dict[str, Any]) -> dict[str, Any]:
    """A JSON-safe deep copy (requests are JSON; anything else raises)."""
    return json.loads(json.dumps(kwargs, ensure_ascii=False))


# ─── Group specs (JSON round trip for --rescore) ─────────────────────────────


def group_to_json(group: ExtractionGroup) -> dict[str, Any]:
    return asdict(group)


def group_from_json(data: dict[str, Any]) -> ExtractionGroup:
    fields = tuple(
        FieldRequest(**{**f, "tab_ids": tuple(f.get("tab_ids") or ())})
        for f in data.get("fields") or ()
    )
    return ExtractionGroup(
        group_id=data["group_id"],
        fields=fields,
        visual=bool(data.get("visual")),
        est_output_tokens=int(data.get("est_output_tokens") or 0),
        tab_ids=tuple(data.get("tab_ids") or ()),
    )


# ─── Cassette capture ────────────────────────────────────────────────────────


class _Recorder:
    """The rows one replay records, and the context the wrappers attach to them."""

    def __init__(self, video_id: str, duration: int) -> None:
        self.video_id = video_id
        self.duration = duration
        self.rows: list[dict[str, Any]] = []
        self._ordinals: dict[tuple[str, str | None], int] = {}

    def record(self, kwargs: dict[str, Any]) -> dict[str, Any]:
        feature = llm_feature_var.get()
        span = fake_llm._span_var.get()
        ordinal = self._ordinals.get((feature, span), 0)
        self._ordinals[(feature, span)] = ordinal + 1
        row = {"feature": feature, "span": span, "ordinal": ordinal, "kwargs": plain(kwargs)}
        row["context"] = dict(_group_ctx.get() or {})
        self.rows.append(row)
        return row

    def note_plan(self, video: Any) -> None:
        """Attach the plan's normalisation inputs to the latest plan request."""
        for row in reversed(self.rows):
            if row["feature"] == "summarize:plan":
                row["context"].update(
                    category_hint=video.category_hint, content_format=video.content_format
                )
                return


async def _tap_usage(stream: Any, row: dict[str, Any]) -> AsyncIterator[Any]:
    """Pass a streamed answer through, keeping the output tokens its usage chunk carries."""
    async for chunk in stream:
        usage = getattr(chunk, "usage", None)
        if usage is not None:
            row["output_tokens"] = int(getattr(usage, "completion_tokens", 0) or 0)
        yield chunk


def _recording_patches(recorder: _Recorder, stack: ExitStack) -> None:
    original_completion = fake_llm.ReplayLLM.acompletion
    original_plan = plan._plan_from_response
    original_group = extraction_groups.run_group_call

    async def recording(self: fake_llm.ReplayLLM, **kwargs: Any) -> Any:
        row = recorder.record(kwargs)
        answer = await original_completion(self, **kwargs)
        if kwargs.get("stream"):
            return _tap_usage(answer, row)
        row["output_tokens"] = int(getattr(answer.usage, "completion_tokens", 0) or 0)
        return answer

    def plan_from_response(raw: str, video: Any) -> Any:
        recorder.note_plan(video)
        return original_plan(raw, video)

    async def group_call(call: Any, run: Any, **kwargs: Any) -> Any:
        token = _group_ctx.set(
            {
                "group": group_to_json(call.group),
                "content_tags": list(run.content_tags),
                "modifiers": list(run.modifiers),
                "chunk": call.chunk.index,
            }
        )
        try:
            return await original_group(call, run, **kwargs)
        finally:
            _group_ctx.reset(token)

    stack.enter_context(patch.object(fake_llm.ReplayLLM, "acompletion", recording))
    stack.enter_context(patch.object(plan, "_plan_from_response", plan_from_response))
    stack.enter_context(patch.object(extraction_groups, "run_group_call", group_call))


def _to_request(recorder: _Recorder, row: dict[str, Any]) -> AbRequest | None:
    call_type = call_type_of(row["feature"])
    if call_type is None:
        return None
    span = row["span"] or row["feature"]
    out = row.get("output_tokens") or _UNRECORDED_OUTPUT_TOKENS.get(call_type, 0)
    context = {**row["context"], "span": span, "duration": recorder.duration}
    return AbRequest(
        request_id=f"cassette/{recorder.video_id}/{span}#{row['ordinal']}",
        call_type=call_type,
        source="cassette",
        video_id=recorder.video_id,
        kwargs=row["kwargs"],
        expected_output_tokens=int(out),
        context=context,
    )


def _todays_rules(prompt: ExtractionPrompt) -> ExtractionPrompt:
    """Round 1's TODAY rules: the group rules (4.0 picks) are the completeness variant."""
    return prompt


async def _capture_video(video_id: str) -> list[AbRequest]:
    cassette = load_cassette(video_id)
    recorder = _Recorder(video_id, cassette.video.duration)
    with ExitStack() as stack:
        _recording_patches(recorder, stack)
        stack.enter_context(patch.object(split_extraction, "with_group_rules", _todays_rules))
        await run_replay(cassette, speed=0.0, settings_overrides=_CAPTURE_OVERRIDES)
    requests = [_to_request(recorder, row) for row in recorder.rows]
    return [request for request in requests if request is not None]


def check_model_agnostic(requests: list[AbRequest]) -> None:
    """Raise when a captured request is not TODAY's model or carries a model's own params."""
    for request in requests:
        model = request.kwargs.get("model")
        extra = sorted(_MODEL_PARAMS & set(request.kwargs))
        if model != TODAY[request.call_type] or extra:
            raise ValueError(f"{request.request_id}: captured for {model} with {extra}")


def capture_cassettes(videos: tuple[str, ...] = CASSETTE_VIDEOS) -> list[AbRequest]:
    """Every request the A/B sends from the replay cassettes, in call order per video."""
    captured: list[AbRequest] = []
    for video_id in videos:
        captured.extend(asyncio.run(_capture_video(video_id)))
    check_model_agnostic(captured)
    return captured


# ─── Direct builders ─────────────────────────────────────────────────────────


def canned_response(model: str, content: str) -> ModelResponse:
    """A LiteLLM-shaped answer, as a provider would return it."""
    return ModelResponse(
        model=model.rsplit("/", 1)[-1],
        choices=[
            {
                "index": 0,
                "finish_reason": "stop",
                "message": {"role": "assistant", "content": content},
            }
        ],
        usage={"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
    )


@contextmanager
def recording_provider(answer: Callable[[dict[str, Any]], str]) -> Iterator[list[dict[str, Any]]]:
    """Record every request the provider sends; each is answered with ``answer(kwargs)``."""
    captured: list[dict[str, Any]] = []

    async def fake_completion(**kwargs: Any) -> ModelResponse:
        captured.append(plain(kwargs))
        return canned_response(str(kwargs["model"]), answer(kwargs))

    with patch("src.services.llm_provider.acompletion", fake_completion):
        yield captured
