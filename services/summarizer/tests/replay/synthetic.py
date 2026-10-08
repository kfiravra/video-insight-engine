"""Synthesized LLM entries for stages the recorded runs predate (pipeline-1min 1b).

The three cassettes were recorded before the tier probe and the memory stage
existed. Their entries are synthesized from what the runs did record and
marked ``synthetic: true``:

* ``tier_probe#0`` replaces the recorded ``classifier`` call (retired in
  1b.1): domain, format and confidence copied, ``has_visual_demo`` judged per
  video. Gate-0 tier-probe A/B, Haiku 4.5 at temperature 0, bare JSON:
  ≈ 1.0 s per call (0.89–1.03 s without the reasoning tail, max 1.64 s with
  it), ≈ 1,800 input / 40 output tokens.
* ``memory#0`` (1b.3) answers from the recorded run itself: tldr + takeaways
  from its synthesis output, the outline from its YouTube chapters or else
  ``EVEN_SECTIONS`` even sections titled by their first spoken words, no
  evidence keys (the readers then use the plan's). Wall: the brief's
  assumption — a Haiku answer of ``MEMORY_OUTPUT_TOKENS`` at ≈ 100 tok/s
  (the measured extraction rate is 101–112 tok/s), so 12 s, the top of the
  brief's 9–12 s; input ≈ transcript chars / 4 + the prompt.

* ``frame_vision#0..n-1`` (1d.4) split the recorded single vision call into
  the per-batch calls the batched analyzer makes (``plan_vision_batches``,
  ordinal = batch order = call order). Each batch answers its frames with the
  recorded descriptions, relabelled 0..k-1 in the batch's own order. Tokens:
  output by the batch's share of the recorded answer, input by its share of
  the frames (the repeated text prompt is ignored — it is small next to the
  images). Wall: 6.6 s + 16.2 s per 1k output tokens (A-DIGEST vision fit).

The committed cassettes were converted with these functions, once (vision:
with the frames each replay hands the analyzer). A cassette rebuilt by
``build_cassette`` from a dump of a pre-1b / pre-1d.4 run needs the same
conversion; runs recorded after those tasks carry the calls themselves.
"""

from __future__ import annotations

import json
from typing import Any

PROBE_FEATURE = "summarize:tier_probe"
PROBE_SPAN = "tier_probe"
PROBE_MODEL = "anthropic/claude-haiku-4-5-20251001"
PROBE_LATENCY_MS = 1000
PROBE_USAGE = {"input": 1800, "output": 40, "cacheRead": 0, "cacheWrite": 0}
CLASSIFIER_SPAN = "classifier"

MEMORY_FEATURE = "summarize:memory"
MEMORY_SPAN = "memory"
MEMORY_MODEL = "anthropic/claude-haiku-4-5-20251001"  # LLM_EXTRACTION_MODEL in the cassettes
MEMORY_OUTPUT_TOKENS = 1200
MEMORY_TOKENS_PER_SECOND = 100
MEMORY_PROMPT_TOKENS = 1500
CHARS_PER_TOKEN = 4
EVEN_SECTIONS = 6
TITLE_WORDS = 6

PROBE_NOTE = (
    "tier_probe#0 is SYNTHETIC (task 1b.1): the recorded run called the retired "
    "classifier instead; its domain/format/confidence are kept, has_visual_demo is "
    "judged from the video, and wall/tokens are the gate-0 A/B Haiku numbers "
    f"({PROBE_LATENCY_MS} ms, {PROBE_USAGE['input']} in / {PROBE_USAGE['output']} out). "
    "See tests/replay/synthetic.py."
)


def probe_output(classifier_output: str, has_visual_demo: bool) -> str:
    """The probe's bare-JSON answer from a recorded classifier answer."""
    recorded = json.loads(classifier_output)
    return json.dumps(
        {
            "domain": recorded["domain"],
            "format": recorded["format"],
            "has_visual_demo": has_visual_demo,
            "confidence": recorded["confidence"],
        }
    )


def probe_entry(classifier_entry: dict[str, Any], has_visual_demo: bool) -> dict[str, Any]:
    """The cassette's ``tier_probe#0`` entry replacing a recorded ``classifier`` entry."""
    return {
        "feature": PROBE_FEATURE,
        "span": PROBE_SPAN,
        "model": PROBE_MODEL,
        "latencyMs": PROBE_LATENCY_MS,
        "output": probe_output(classifier_entry["output"], has_visual_demo),
        "finishReason": "stop",
        "usage": dict(PROBE_USAGE),
        "ordinal": 0,
        "synthetic": True,
    }


def replace_classifier_entries(
    entries: list[dict[str, Any]], has_visual_demo: bool
) -> list[dict[str, Any]]:
    """``entries`` with each recorded classifier call swapped for its probe entry."""
    return [
        probe_entry(entry, has_visual_demo) if entry["span"] == CLASSIFIER_SPAN else entry
        for entry in entries
    ]


MEMORY_NOTE = (
    "memory#0 is SYNTHETIC (task 1b.3): the recorded run predates the memory call; "
    "tldr/takeaways come from its synthesis output, the outline from its chapters "
    f"(else {EVEN_SECTIONS} even sections), no evidence; wall = {MEMORY_OUTPUT_TOKENS} "
    f"output tokens at {MEMORY_TOKENS_PER_SECOND} tok/s. See tests/replay/synthetic.py."
)


def _clock(seconds: int) -> str:
    minutes, secs = divmod(max(0, seconds), 60)
    return f"{minutes}:{secs:02d}"


def _first_words(segments: list[list[Any]], at_ms: int) -> str:
    text = next((seg[2] for seg in segments if seg[0] >= at_ms), "")
    return " ".join(str(text).split()[:TITLE_WORDS]) or "section"


def _outline(cassette: dict[str, Any]) -> list[dict[str, str]]:
    """The recorded YouTube chapters, else even sections titled by their first words."""
    video = cassette["video"]
    chapters = video.get("chapters") or []
    if chapters:
        return [
            {"start": _clock(c["start"]), "end": _clock(c["end"]), "title": c["title"]}
            for c in chapters
        ]
    duration = int(video["duration"])
    bounds = [duration * i // EVEN_SECTIONS for i in range(EVEN_SECTIONS + 1)]
    segments = cassette["transcript"]["segments"]
    return [
        {"start": _clock(start), "end": _clock(end), "title": _first_words(segments, start * 1000)}
        for start, end in zip(bounds, bounds[1:], strict=False)
    ]


def memory_output(cassette: dict[str, Any]) -> str:
    """The memory answer the recorded run would have produced (see the module docstring)."""
    synthesis = next(e for e in cassette["llm"] if e["span"] == "synthesis")
    recorded = json.loads(synthesis["output"])
    return json.dumps(
        {
            "outline": _outline(cassette),
            "evidence": {},
            "tldr": recorded.get("tldr", ""),
            "takeaways": recorded.get("keyTakeaways", [])[:5],
        },
        ensure_ascii=False,
    )


def memory_entry(cassette: dict[str, Any]) -> dict[str, Any]:
    """The cassette's ``memory#0`` entry."""
    transcript_chars = sum(len(seg[2]) for seg in cassette["transcript"]["segments"])
    return {
        "feature": MEMORY_FEATURE,
        "span": MEMORY_SPAN,
        "model": MEMORY_MODEL,
        "latencyMs": MEMORY_OUTPUT_TOKENS * 1000 // MEMORY_TOKENS_PER_SECOND,
        "output": memory_output(cassette),
        "finishReason": "stop",
        "usage": {
            "input": transcript_chars // CHARS_PER_TOKEN + MEMORY_PROMPT_TOKENS,
            "output": MEMORY_OUTPUT_TOKENS,
            "cacheRead": 0,
            "cacheWrite": 0,
        },
        "ordinal": 0,
        "synthetic": True,
    }


VISION_SPAN = "frame_vision"
VISION_BASE_MS = 6600
VISION_MS_PER_OUTPUT_TOKEN = 16.2  # 16.2 s per 1k output tokens
VISION_NOTE = (
    "frame_vision#0..n-1 are SYNTHETIC (task 1d.4): the recorded single vision call split "
    "into the batched analyzer's calls (plan_vision_batches), descriptions relabelled per "
    "batch; tokens by output/frame share, wall 6.6 s + 16.2 s per 1k output tokens. "
    "See tests/replay/synthetic.py."
)


def _label_order(frames: list[dict[str, Any]], max_frames: int) -> list[dict[str, Any]]:
    """The recorded call's label order: top ``max_frames`` by score (``_select_top_frames``)."""
    return sorted(frames, key=lambda f: f.get("total_score", 0), reverse=True)[:max_frames]


def _batch_answers(
    entry: dict[str, Any], frames: list[dict[str, Any]], max_frames: int
) -> list[list[dict[str, Any]]]:
    """Per planned batch: the recorded descriptions of its frames, relabelled 0..k-1."""
    from src.services.media.frame_analyzer import plan_vision_batches

    labelled = _label_order(frames, max_frames)
    label_of = {id(frame): label for label, frame in enumerate(labelled)}
    recorded = {item["frame_index"]: item for item in json.loads(entry["output"])}
    return [
        [
            {**recorded[label_of[id(frame)]], "frame_index": local}
            for local, frame in enumerate(batch)
            if label_of[id(frame)] in recorded
        ]
        for batch in plan_vision_batches(labelled)
    ]


def split_vision_entry(
    entry: dict[str, Any], frames: list[dict[str, Any]], max_frames: int
) -> list[dict[str, Any]]:
    """The recorded single vision call as the batched analyzer's calls (see module docstring).

    ``frames`` are the frames the vision call received (``timestamp``,
    ``total_score``); ``max_frames`` its frame cap.
    """
    answers = _batch_answers(entry, frames, max_frames)
    outputs = [json.dumps(items, ensure_ascii=False) for items in answers]
    total_chars = sum(len(text) for text in outputs) or 1
    total_frames = sum(len(items) for items in answers) or 1
    usage = entry["usage"]
    entries = []
    for ordinal, (items, output) in enumerate(zip(answers, outputs, strict=True)):
        output_tokens = round(usage["output"] * len(output) / total_chars)
        entries.append(
            {
                "feature": entry["feature"],
                "span": entry["span"],
                "model": entry["model"],
                "latencyMs": round(VISION_BASE_MS + VISION_MS_PER_OUTPUT_TOKEN * output_tokens),
                "output": output,
                "finishReason": entry.get("finishReason") or "stop",
                "usage": {
                    **usage,
                    "input": round(usage["input"] * len(items) / total_frames),
                    "output": output_tokens,
                },
                "ordinal": ordinal,
                "synthetic": True,
            }
        )
    return entries
