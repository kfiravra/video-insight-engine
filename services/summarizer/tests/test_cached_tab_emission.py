"""Cache hits stream every stored tab once, final, at its position (pipeline-1min 2.1).

``stream_cached_structured`` serves both the completed-row SSE path and the
Redis response-cache hit: a stored response has nothing left to change.
"""

from __future__ import annotations

import json

from src.routes.cached_response import stream_cached_structured

_ENTRY = {
    "title": "t",
    "meta": {"contentTags": ["tech"], "primaryTag": "tech"},
    "tabs": [
        {"id": "overview", "label": "Overview", "emoji": "", "component": "overview", "props": {}},
        {"id": "code", "label": "Code", "emoji": "", "component": "code_playground", "props": {}},
        {"id": "quiz", "label": "Quiz", "emoji": "", "component": "quiz_arena", "props": {}},
    ],
}


async def _tab_events(entry: dict) -> list[dict]:
    chunks = [c async for c in stream_cached_structured("vsid", entry)]
    payloads = [json.loads(c.removeprefix("data: ")) for c in chunks if c != "data: [DONE]\n\n"]
    return [p for p in payloads if p["event"] == "tab_ready"]


async def test_should_send_every_stored_tab_final_at_its_position() -> None:
    events = await _tab_events(_ENTRY)

    assert [(e["id"], e["position"], e["final"]) for e in events] == [
        ("overview", 0, True),
        ("code", 1, True),
        ("quiz", 2, True),
    ]


async def test_should_send_no_tab_twice() -> None:
    events = await _tab_events(_ENTRY)

    assert len({e["id"] for e in events}) == len(events)


async def test_should_never_mark_a_stored_tab_provisional() -> None:
    events = await _tab_events(_ENTRY)

    assert not any(e.get("provisional") for e in events)


async def test_should_skip_a_malformed_legacy_tab_entry() -> None:
    entry = {**_ENTRY, "tabs": ["legacy-string", *_ENTRY["tabs"][:1]]}

    events = await _tab_events(entry)

    assert [(e["id"], e["position"]) for e in events] == [("overview", 0)]


async def test_should_count_in_meta_only_the_tabs_it_streams() -> None:
    entry = {**_ENTRY, "tabs": ["legacy-string", *_ENTRY["tabs"][:1]]}
    chunks = [c async for c in stream_cached_structured("vsid", entry)]

    meta = next(json.loads(c.removeprefix("data: ")) for c in chunks if '"event": "meta"' in c)

    assert (meta["tabCount"], [t["id"] for t in meta["tabLabels"]]) == (1, ["overview"])
