"""The tab_ready emission contract on the replayed benchmark runs (pipeline-1min 2.1).

Every cassette: the provisional Moments tab streams at plan-done (after the
plan's ``meta``, before extraction), every saved tab gets exactly one final
emission at its saved position, nothing follows a tab's final emission, and
``complete.tabIds`` is the saved order.

T1dQhQAm8Tc as the 2026-10-12 dev run planned it — no moment_track tab — is
the always-Moments acceptance case: the outline-based ``key_moments`` tab
streams provisional at plan-done and is saved as a real tab before the quiz.

Both extraction modes, whatever the process runs (3.6): the split path streams
previews per landed group — non-final, without links or attachments, at their
final positions, never the overview, a moment tab or the quiz — previews the
overview (and whatever else the client lacks) after the final assembly, before
the late group, and sends every tab final once, the overview last; the late
group and faithfulness wait for the last group. ``EXTRACTION_PARALLEL=false``
keeps today's ``tab_ready`` sequence.
"""

from __future__ import annotations

import asyncio
import json
import logging
from collections import Counter
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, replace
from typing import Any
from unittest.mock import patch

import pytest

from src.routes import pipeline_orchestration
from src.services.pipeline import tab_ledger
from src.services.pipeline.context import PipelineContext
from tests.replay.cassette import Cassette, LLMEntry, available_cassettes
from tests.replay.driver import ReplayResult, run_replay

_VIDEOS = available_cassettes()
_REFERENCE_VIDEO = "T1dQhQAm8Tc"
# Plans whose steps are the timeline (D6, output-quality 0b.2): assembly ships no
# Moments tab, and plan-done streams no provisional one (0b follow-ups).
_STEPS_ARE_THE_TIMELINE = frozenset({"jMq8lEu-of0"})
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
        tabs = replays[video_id].tab_events()

        if video_id in _STEPS_ARE_THE_TIMELINE:
            assert [t["id"] for t in tabs if t.get("provisional")] == []
            return
        assert tabs[0].get("provisional") is True

    def test_should_stream_it_at_plan_done_before_extraction(self, replays: dict, video_id: str):
        meta_at, extraction_at = _ends_of_plan_done_window(replays[video_id])

        assert meta_at < _first_tab_index(replays[video_id]) < extraction_at

    def test_should_replace_the_provisional_tab_by_id_with_a_final_one(
        self, replays: dict, video_id: str
    ) -> None:
        tabs = replays[video_id].tab_events()

        if video_id in _STEPS_ARE_THE_TIMELINE:
            assert not any(t.get("provisional") for t in tabs)
            return
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


# ─── The split path: per-group emission (3.6) ───

_HELD_PER_GROUP = {"overview", "moment_track", "quiz_arena"}
_FINAL_ONLY_KEYS = {"crossTabLinks", "attachments"}
_STREAM_KEYS = {"event", "final", "position"}


@dataclass(frozen=True)
class _SplitRun:
    result: ReplayResult
    ledger_errors: list[str]
    # (frames done, group rows recorded) when the run launched the faithfulness judge
    faithfulness: list[tuple[bool, int]]


class _Errors(logging.Handler):
    def __init__(self) -> None:
        super().__init__(level=logging.ERROR)
        self.messages: list[str] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.messages.append(record.getMessage())


@contextmanager
def _ledger_errors() -> Iterator[_Errors]:
    handler = _Errors()
    logger = logging.getLogger(tab_ledger.__name__)
    logger.addHandler(handler)
    try:
        yield handler
    finally:
        logger.removeHandler(handler)


def _split_run(cassette: Cassette) -> _SplitRun:
    launches: list[tuple[bool, int]] = []

    def launch(ctx: PipelineContext) -> None:
        launches.append((ctx.frames_done.is_set(), len(ctx.extraction_groups)))

    with (
        patch.object(pipeline_orchestration, "_launch_faithfulness_check", launch),
        _ledger_errors() as errors,
    ):
        overrides = {"EXTRACTION_PARALLEL": True}
        result = asyncio.run(run_replay(cassette, speed=0, settings_overrides=overrides))
    return _SplitRun(result, errors.messages, launches)


@pytest.fixture(scope="module")
def split_runs(cassettes: dict[str, Cassette]) -> dict[str, _SplitRun]:
    return {video_id: _split_run(cassette) for video_id, cassette in cassettes.items()}


def _per_group_tabs(result: ReplayResult) -> list[dict[str, Any]]:
    """The non-provisional tabs streamed before ``extraction_complete`` (the groups')."""
    cut = result.event_names().index("extraction_complete")
    early = [r.payload() for r in result.events[:cut] if r.event == "tab_ready"]
    return [tab for tab in early if not tab.get("provisional")]


def _tab_event_indexes(result: ReplayResult, component: str) -> list[int]:
    return [
        i
        for i, r in enumerate(result.events)
        if r.event == "tab_ready"
        and r.payload().get("component") == component
        and not r.payload().get("provisional")
    ]


@pytest.mark.parametrize("video_id", _VIDEOS)
class TestSplitPathEmission:
    def test_should_stream_per_group_tabs_non_final(self, split_runs: dict, video_id: str):
        tabs = _per_group_tabs(split_runs[video_id].result)

        assert {tab["final"] for tab in tabs} == {False}

    def test_should_never_stream_the_overview_a_moment_tab_or_the_quiz_per_group(
        self, split_runs: dict, video_id: str
    ) -> None:
        tabs = _per_group_tabs(split_runs[video_id].result)

        assert {tab["component"] for tab in tabs} & _HELD_PER_GROUP == set()

    def test_should_strip_links_and_attachments_from_per_group_tabs(
        self, split_runs: dict, video_id: str
    ) -> None:
        tabs = _per_group_tabs(split_runs[video_id].result)

        assert [tab["id"] for tab in tabs if _FINAL_ONLY_KEYS & set(tab)] == []

    def test_should_preview_each_tab_at_its_final_position(self, split_runs: dict, video_id: str):
        tabs = split_runs[video_id].result.tab_events()
        finals = {t["id"]: t["position"] for t in tabs if t["final"]}

        moved = [
            t["id"] for t in tabs if not t.get("provisional") and t["position"] != finals[t["id"]]
        ]

        assert moved == []

    def test_should_preview_the_overview_before_the_late_group(
        self, split_runs: dict, video_id: str
    ) -> None:
        result = split_runs[video_id].result
        names = result.event_names()
        overview = next(r.payload() for r in result.events if r.payload().get("id") == "overview")
        overview_at = next(
            i for i, r in enumerate(result.events) if r.payload().get("id") == "overview"
        )
        synthesis = max(i for i, name in enumerate(names) if name == "synthesis_complete")

        assert (overview["final"], overview_at < synthesis) == (False, True)

    def test_should_send_every_tab_final_once_after_the_last_preview(
        self, split_runs: dict, video_id: str
    ) -> None:
        result = split_runs[video_id].result
        flags = [tab["final"] for tab in result.tab_events()]
        saved = len(_saved_ids(result))

        assert flags == [False] * (len(flags) - saved) + [True] * saved

    def test_should_send_the_overview_last(self, split_runs: dict, video_id: str):
        assert split_runs[video_id].result.tab_events()[-1]["id"] == "overview"

    def test_should_resend_unchanged_content_only_while_a_late_phase_is_pending(
        self, split_runs: dict, video_id: str
    ) -> None:
        result = split_runs[video_id].result
        quiz_pending = "enrichment_complete" in result.event_names()
        last: dict[str, str] = {}
        repeats = []
        for tab in result.tab_events():
            content = json.dumps(
                {k: v for k, v in tab.items() if k not in _STREAM_KEYS}, sort_keys=True
            )
            pending = quiz_pending or tab["id"] == "overview"  # a late synthesis patches it
            if last.get(tab["id"]) == content and not pending:
                repeats.append(tab["id"])
            last[tab["id"]] = content

        assert repeats == []

    def test_should_log_no_second_final(self, split_runs: dict, video_id: str):
        assert split_runs[video_id].ledger_errors == []

    def test_should_fill_the_moments_after_the_last_group(self, split_runs: dict, video_id: str):
        result = split_runs[video_id].result
        last_group = result.event_names().index("extraction_complete")

        if video_id in _STEPS_ARE_THE_TIMELINE:
            assert _tab_event_indexes(result, "moment_track") == []
            return
        assert min(_tab_event_indexes(result, "moment_track")) > last_group

    def test_should_synthesize_after_the_last_group(self, split_runs: dict, video_id: str):
        names = split_runs[video_id].result.event_names()
        synthesis = max(i for i, name in enumerate(names) if name == "synthesis_complete")

        assert synthesis > names.index("extraction_complete")

    def test_should_launch_faithfulness_once_every_group_landed(
        self, split_runs: dict, video_id: str
    ) -> None:
        run = split_runs[video_id]

        assert run.faithfulness == [(True, run.result.group_calls)]


# ─── EXTRACTION_PARALLEL=false: today's tab_ready sequence ───

# (id, position, final, provisional, replaces) per tab_ready, captured on b7a2077;
# jMq8lEu-of0 lost its provisional key_moments preview to D6 (output-quality 0b).
_TODAYS_SEQUENCE: dict[str, list[tuple[str, int, bool, bool, str | None]]] = {
    # output-quality 1.2: the cassette's label-only learning.timestamps no longer
    # validate (a moment states what it established), so key_moments comes from the
    # fallback chain with fewer frames and the filmstrip joins at 5.
    "T1dQhQAm8Tc": [
        ("key_moments", 3, False, True, None),
        ("overview", 0, False, False, None),
        ("stack_explorer", 1, False, False, None),
        ("code_snippets", 2, False, False, None),
        ("concepts", 4, False, False, None),
        ("frames-gallery", 5, False, False, None),
        ("key_moments", 3, False, False, None),
        ("overview", 0, True, False, None),
        ("stack_explorer", 1, True, False, None),
        ("code_snippets", 2, True, False, None),
        ("key_moments", 3, True, False, None),
        ("concepts", 4, True, False, None),
        ("frames-gallery", 5, True, False, None),
        ("quiz", 6, True, False, None),
    ],
    # output-quality 1.6 (R6): the planned chef_tips grid rides inside cook_steps.
    "jMq8lEu-of0": [
        ("overview", 0, False, False, None),
        ("ingredients", 1, True, False, None),
        ("cook_steps", 2, True, False, None),
        ("frames-gallery", 3, True, False, None),
        ("overview", 0, True, False, None),
    ],
    "uC45_4nnEAI": [
        ("highlights", 2, False, True, None),
        ("overview", 0, False, False, None),
        ("pulls", 1, True, False, None),
        ("set_facts", 3, True, False, None),
        ("frames-gallery", 4, True, False, None),
        ("highlights", 2, True, False, None),
        ("overview", 0, True, False, None),
    ],
}


@pytest.fixture(scope="module")
def false_path_runs(cassettes: dict[str, Cassette]) -> dict[str, ReplayResult]:
    overrides = {"EXTRACTION_PARALLEL": False}
    return {
        video_id: asyncio.run(run_replay(cassette, speed=0, settings_overrides=overrides))
        for video_id, cassette in cassettes.items()
    }


@pytest.mark.parametrize("video_id", _VIDEOS)
def test_should_keep_todays_tab_ready_sequence_when_extraction_parallel_is_false(
    false_path_runs: dict, video_id: str
) -> None:
    sequence = [
        (t["id"], t["position"], t["final"], t.get("provisional", False), t.get("replaces"))
        for t in false_path_runs[video_id].tab_events()
    ]

    assert sequence == _TODAYS_SEQUENCE[video_id]
