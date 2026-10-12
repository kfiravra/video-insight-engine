"""The tab_ready emission contract on the replayed benchmark runs (pipeline-1min 2.1).

Every cassette: the provisional Moments tab streams at plan-done (after the
plan's ``meta``, before extraction), every saved tab gets exactly one final
emission at its saved position, nothing follows a tab's final emission, and
``complete.tabIds`` is the saved order.

T1dQhQAm8Tc as the 2026-10-12 dev run planned it — no moment_track tab — is
the always-Moments acceptance case: the outline-based ``key_moments`` tab
streams provisional at plan-done and is saved as a real tab before the quiz.
"""

from __future__ import annotations

import asyncio
import json
from collections import Counter
from dataclasses import replace
from typing import Any

import pytest

from tests.replay.cassette import Cassette, LLMEntry, available_cassettes
from tests.replay.driver import ReplayResult, run_replay

_VIDEOS = available_cassettes()
_REFERENCE_VIDEO = "T1dQhQAm8Tc"
_PLAN_SPAN = "plan"
_MEMORY_SPAN = "memory"
_FENCE = "```"
# T1dQhQAm8Tc's planned tabs other than its moment_track tab and the quiz.
_PLANNED_BEFORE_THE_QUIZ = ("stack_explorer", "code_snippets", "concepts")


def _saved_ids(result: ReplayResult) -> list[str]:
    return [t["id"] for t in (result.saved_result or {}).get("tabs", [])]


def _complete(result: ReplayResult) -> dict[str, Any]:
    return next(r.payload() for r in result.events if r.event == "complete")


def _first_tab_index(result: ReplayResult) -> int:
    return result.event_names().index("tab_ready")


def _ends_of_plan_done_window(result: ReplayResult) -> tuple[int, int]:
    """(index of the plan's ``meta``, index of ``extraction_complete``)."""
    names = result.event_names()
    return names.index("meta"), names.index("extraction_complete")


@pytest.mark.parametrize("video_id", _VIDEOS)
class TestTabEmissionContract:
    def test_should_open_with_the_provisional_moments_tab(self, replays: dict, video_id: str):
        assert replays[video_id].tab_events()[0].get("provisional") is True

    def test_should_stream_it_at_plan_done_before_extraction(self, replays: dict, video_id: str):
        meta_at, extraction_at = _ends_of_plan_done_window(replays[video_id])

        assert meta_at < _first_tab_index(replays[video_id]) < extraction_at

    def test_should_replace_the_provisional_tab_by_id_with_a_final_one(
        self, replays: dict, video_id: str
    ) -> None:
        tabs = replays[video_id].tab_events()

        provisional_id = tabs[0]["id"]

        assert [t["final"] for t in tabs if t["id"] == provisional_id][-1] is True

    def test_should_send_every_saved_tab_final_exactly_once(self, replays: dict, video_id: str):
        finals = Counter(t["id"] for t in replays[video_id].tab_events() if t["final"])

        assert finals == Counter(_saved_ids(replays[video_id]))

    def test_should_send_final_tabs_at_their_saved_positions(self, replays: dict, video_id: str):
        finals = {t["id"]: t["position"] for t in replays[video_id].tab_events() if t["final"]}

        assert finals == {tab_id: i for i, tab_id in enumerate(_saved_ids(replays[video_id]))}

    def test_should_send_nothing_for_a_tab_after_its_final_emission(
        self, replays: dict, video_id: str
    ) -> None:
        settled: set[str] = set()
        after_final = []
        for tab in replays[video_id].tab_events():
            if tab["id"] in settled:
                after_final.append(tab["id"])
            if tab["final"]:
                settled.add(tab["id"])

        assert after_final == []

    def test_should_never_repeat_an_emission_verbatim(self, replays: dict, video_id: str):
        last: dict[str, str] = {}
        repeats = []
        for tab in replays[video_id].tab_events():
            body = json.dumps(tab, sort_keys=True)
            if last.get(tab["id"]) == body:
                repeats.append(tab["id"])
            last[tab["id"]] = body

        assert repeats == []

    def test_should_send_every_tab_before_complete(self, replays: dict, video_id: str):
        names = replays[video_id].event_names()

        assert max(i for i, n in enumerate(names) if n == "tab_ready") < names.index("complete")

    def test_should_list_the_saved_tab_order_on_complete(self, replays: dict, video_id: str):
        assert _complete(replays[video_id])["tabIds"] == _saved_ids(replays[video_id])


# ─── T1dQhQAm8Tc without a planned Moments tab (the dev run of 2026-10-12) ───


def _unfenced(output: str) -> str:
    body = output.strip()
    if body.startswith(_FENCE):
        body = body.split("\n", 1)[1].rsplit(_FENCE, 1)[0]
    return body


def _without_moment_tabs(entry: LLMEntry) -> LLMEntry:
    plan = json.loads(_unfenced(entry.output))
    plan["tabs"] = [t for t in plan["tabs"] if t.get("component") != "moment_track"]
    return replace(entry, output=json.dumps(plan, ensure_ascii=False))


def without_planned_moments(cassette: Cassette) -> Cassette:
    """``cassette`` whose plan designed no moment_track tab."""
    llm = [_without_moment_tabs(e) if e.key.span == _PLAN_SPAN else e for e in cassette.llm]
    return replace(cassette, llm=llm)


@pytest.fixture(scope="module")
def unplanned(cassettes: dict[str, Cassette]) -> ReplayResult:
    cassette = without_planned_moments(cassettes[_REFERENCE_VIDEO])
    return asyncio.run(run_replay(cassette, speed=0))


def _outline_titles(cassette: Cassette) -> list[str]:
    memory = next(e for e in cassette.llm if e.key.span == _MEMORY_SPAN)
    return [section["title"] for section in json.loads(memory.output)["outline"]]


class TestMomentsTabWithoutAPlannedOne:
    def test_should_stream_a_provisional_key_moments_tab_first(self, unplanned: ReplayResult):
        first = unplanned.tab_events()[0]

        assert (first["id"], first["provisional"]) == ("key_moments", True)

    def test_should_stream_it_at_plan_done_before_extraction(self, unplanned: ReplayResult):
        meta_at, extraction_at = _ends_of_plan_done_window(unplanned)

        assert meta_at < _first_tab_index(unplanned) < extraction_at

    def test_should_save_the_key_moments_tab_after_the_planned_tabs(self, unplanned: ReplayResult):
        ids = _saved_ids(unplanned)

        assert ids.index("key_moments") > max(ids.index(t) for t in _PLANNED_BEFORE_THE_QUIZ)

    def test_should_keep_the_quiz_last(self, unplanned: ReplayResult):
        assert _saved_ids(unplanned)[-1] == "quiz"

    def test_should_fill_it_from_the_memory_outline(
        self, unplanned: ReplayResult, cassettes: dict[str, Cassette]
    ) -> None:
        saved = next(t for t in (unplanned.saved_result or {})["tabs"] if t["id"] == "key_moments")

        labels = [item["label"] for item in saved["props"]["items"]]

        assert labels == _outline_titles(cassettes[_REFERENCE_VIDEO])

    def test_should_send_it_final_once_at_its_saved_position(self, unplanned: ReplayResult):
        finals = [
            t["position"] for t in unplanned.tab_events() if t["id"] == "key_moments" and t["final"]
        ]

        assert finals == [_saved_ids(unplanned).index("key_moments")]

    def test_should_list_it_on_complete(self, unplanned: ReplayResult):
        assert "key_moments" in _complete(unplanned)["tabIds"]
