"""Reviewer dumps of the model A/B round 2 (pipeline-1min 4.0d, D30): extraction items to classify.

Per cassette, for every LIST field a group requested: each extraction output's
items as the pipeline keeps them (``read_group_answer`` → ``validate_group``),
with stable ids ``<arm>.<run>.<field>.<n>`` — every round-2 output (the four
``+rule`` arms ×2) plus Haiku 4.5's no-rule runs (round 1's, and round 2's
extra run on jMq8 + uC45) — and the transcript the groups saw (the
``<transcript>`` of the request's video block; the visual group also gets its
``<visual_annotations>`` and ``<key_frames>``). A reviewer agent returns
``review/<vid>.classified.json``: per field, clusters of items that are the
same step / item across outputs, each ``supported`` / ``padding`` / ``unclear``
with the transcript evidence.
"""

from __future__ import annotations

import json
import re
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from _model_ab_capture import group_from_json
from _model_ab_r2_rows import Row
from _model_ab_scores_items import read_group_answer
from _model_ab_types import AbRequest

from src.services.pipeline.field_requests import FieldRequest

REVIEW_ARMS: tuple[str, ...] = ("A", "A+rule", "B+rule", "B-medium+rule", "C+rule")
_VISUAL_TAGS = ("visual_annotations", "key_frames")
CLASSIFIED_FORMAT = {
    "<field>": [
        {
            "cluster": "<id>",
            "members": ["<item id>", "..."],
            "verdict": "supported | padding | unclear",
            "evidence": "<transcript quote> @ <mm:ss>",
        }
    ]
}


def element_at_line(text: str, tag: str) -> str:
    """The ``<tag>`` … ``</tag>`` element whose tags open their own lines ("" when absent).

    Line-anchored: a guide line that merely names ``<tag>`` is not the element.
    """
    found = re.search(rf"(?ms)^<{tag}>$.*?^</{tag}>$", text)
    return found.group(0) if found else ""


def _inner(text: str, tag: str) -> str:
    """The text between the element's own tag lines."""
    element = element_at_line(text, tag)
    return element[len(tag) + 3 : -(len(tag) + 4)] if element else ""


def _blocks(request: AbRequest) -> list[str]:
    return [str(block.get("text", "")) for block in request.kwargs["messages"][0]["content"]]


def transcript_of(request: AbRequest) -> str:
    """The transcript the group saw: block 2 (the chunk's video + transcript block)."""
    return _inner(_blocks(request)[1], "transcript")


def visual_context_of(request: AbRequest) -> str:
    """The visual group's frame inputs from its job block (annotations + key frames)."""
    job = _blocks(request)[-1]
    found = (element_at_line(job, tag) for tag in _VISUAL_TAGS)
    return "\n\n".join(element for element in found if element)


def _items_at(validated: Mapping[str, Any], field: FieldRequest) -> list[Any] | None:
    node: Any = validated.get(field.domain)
    for key in field.field.split("."):
        if not isinstance(node, Mapping):
            return None
        node = node.get(key)
    return node if isinstance(node, list) else None


def _outputs(rows: Sequence[Row], request_id: str) -> list[Row]:
    found = [
        r
        for r in rows
        if r["request_id"] == request_id
        and r["arm"] in REVIEW_ARMS
        and not r["error"]
        and not r["refusal"]
    ]
    return sorted(found, key=lambda r: (REVIEW_ARMS.index(r["arm"]), r["repeat"]))


def _output_entry(row: Row, field: FieldRequest, context: Mapping[str, Any]) -> dict[str, Any]:
    output = f"{row['arm']}.{row['repeat']}"
    read = read_group_answer(row["raw"], context)
    items = _items_at(read[2], field) if read is not None else None
    return {
        "output": output,
        "arm": row["arm"],
        "run": row["repeat"],
        "round": row["round"],
        "model": row["model"],
        "effort": row["effort"],
        "parsed": read is not None,
        "items": [
            {"id": f"{output}.{field.path}.{n}", "item": item} for n, item in enumerate(items or [])
        ],
    }


def _field_entries(request: AbRequest, rows: Sequence[Row]) -> dict[str, dict[str, Any]]:
    context = request.context
    group = context["group"]
    lists = [f for f in group_from_json(group).fields if f.kind == "list"]
    outputs = _outputs(rows, request.request_id)
    entries: dict[str, dict[str, Any]] = {}
    for field in lists:
        entry: dict[str, Any] = {
            "group": group["group_id"],
            "request_id": request.request_id,
            "cap": field.cap,
            "outputs": [_output_entry(row, field, context) for row in outputs],
        }
        if group.get("visual"):
            entry["visual_context"] = visual_context_of(request)
        entries[field.path] = entry
    return entries


def review_dump(
    video_id: str, requests: Sequence[AbRequest], rows: Sequence[Row]
) -> dict[str, Any]:
    """The reviewer dump of one cassette (JSON-safe)."""
    groups = [r for r in requests if r.video_id == video_id and r.call_type == "extraction"]
    transcripts = {transcript_of(r) for r in groups}
    if len(transcripts) != 1:
        raise ValueError(f"{video_id}: the groups saw {len(transcripts)} different transcripts")
    fields: dict[str, dict[str, Any]] = {}
    for request in groups:
        found = _field_entries(request, rows)
        clash = set(found) & set(fields)
        if clash:
            raise ValueError(f"{video_id}: fields requested by two groups: {sorted(clash)}")
        fields |= found
    return {
        "video_id": video_id,
        "transcript": transcripts.pop(),
        "fields": fields,
        "classified_format": CLASSIFIED_FORMAT,
    }


# ─── Readable copy + sizes ───────────────────────────────────────────────────

_HOW_TO = """For every field below: cluster the items that are the same step / item / command
across the outputs (an output may hold it once, split it in two, or lack it), then give each
cluster one verdict — `supported` (the transcript says it; quote it with its [m:ss]),
`padding` (not in the transcript, or a generic filler), `unclear`. Every item id belongs to
exactly one cluster. Write `review/<vid>.classified.json` as:"""


def _output_heading(output: Mapping[str, Any]) -> str:
    model = str(output["model"]).split("/")[-1]
    state = "" if output["parsed"] else " — DID NOT PARSE"
    return (
        f"#### {output['output']} (round {output['round']}, {model} {output['effort']}) — "
        f"{len(output['items'])} items{state}"
    )


def render_review_md(dump: Mapping[str, Any]) -> str:
    """The dump as a readable markdown file (the same ids as the JSON)."""
    lines = [f"# Reviewer dump — {dump['video_id']}", "", _HOW_TO, "", "```json"]
    lines += [json.dumps(dump["classified_format"], indent=1), "```", ""]
    lines += [
        "## Transcript (what every group of this video saw)",
        "",
        "```",
        dump["transcript"],
        "```",
        "",
    ]
    for path, entry in dump["fields"].items():
        lines += [f"## Field `{path}` (group {entry['group']}, cap {entry['cap']})", ""]
        if entry.get("visual_context"):
            lines += [
                "### Frames this group also saw",
                "",
                "```",
                entry["visual_context"],
                "```",
                "",
            ]
        for output in entry["outputs"]:
            lines += [_output_heading(output), ""]
            lines += [
                f"- `{item['id']}` {json.dumps(item['item'], ensure_ascii=False)}"
                for item in output["items"]
            ]
            lines.append("")
    return "\n".join(lines) + "\n"


def dump_sizes(dump: Mapping[str, Any]) -> dict[str, int]:
    """Fields, outputs (per field, summed) and items of one dump."""
    fields = dump["fields"].values()
    return {
        "fields": len(dump["fields"]),
        "outputs": sum(len(f["outputs"]) for f in fields),
        "items": sum(len(o["items"]) for f in fields for o in f["outputs"]),
        "transcript_chars": len(dump["transcript"]),
    }


def write_review(folder: Path, dump: Mapping[str, Any]) -> tuple[Path, Path]:
    """``<vid>.json`` + ``<vid>.md`` in ``folder``."""
    folder.mkdir(parents=True, exist_ok=True)
    as_json = folder / f"{dump['video_id']}.json"
    as_md = folder / f"{dump['video_id']}.md"
    as_json.write_text(json.dumps(dump, ensure_ascii=False, indent=1), encoding="utf-8")
    as_md.write_text(render_review_md(dump), encoding="utf-8")
    return as_json, as_md
