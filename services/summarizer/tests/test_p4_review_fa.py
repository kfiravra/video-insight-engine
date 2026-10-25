"""Phase-4 review, lane FA — the order guard one-sided, timeline items kept, routing scoped.

- A-C1: the 4.0 order guard moved stamps both ways. On S15's verification run
  (``extraction_text3_c1``) the closing claim "Nakamoto is an exceptionally gifted
  person…", said at 56:24 (3384) and listed last after the Elon Musk aside (4121), was
  moved to 4121. Every miss the G3-3 diagnosis found was LATE, so only a late stamp moves,
  and never one whose verified quote holds it.
- A-W1: a moment or segment pulled back onto its neighbour's second was refused by
  ``normalize_moment_items`` (one item per second). It now takes the first free second;
  ``podcast.qa`` is left to the question check, so a translated question keeps its row.
- A-W4: a planned ``podcast.qa`` / ``podcast.guests`` switches podcast on only on an
  interview or podcast run, never for a talk's audience questions.
- A-W3: the probe's interview line names no series and keeps a solo talk with end
  questions a talk (Kfir 2026-10-23: route on what the transcript shows; S14 probes talk).
"""

from __future__ import annotations

from typing import Any

import pytest

from src.services.pipeline import tier_probe
from src.services.pipeline.assembly.moment_normalizers import normalize_moment_items
from src.services.pipeline.assembly.qa_questions import check_questions
from src.services.pipeline.extraction_chunks import ExtractionChunk, StampReport
from src.services.pipeline.plan import _normalize_plan_data
from src.services.pipeline.plan_reconcile import activate_argument_domains, reconcile_plan_tabs
from src.services.pipeline.quote_verify import QuoteIndex
from src.services.pipeline.stamp_anchor import anchor_stamps

# S15's second chunk (40:02–1:11:22).
_CHUNK_2 = ExtractionChunk(1, "t", start_s=2402.0, end_s=None, video_end_s=4282.0)

# S15 p4v ``extraction_text3_c1`` learning.claims as the call stamped them; the last
# one (3381, the closing claim) is right and listed after 1:08:41. Their quotes are
# English renderings of a Hebrew talk (TRANSLATED), so none anchors.
_S15_CLAIMS = [3323, 3463, 3463, 3541, 3622, 3743, 3822, 3865, 3901, 4081, 4040, 4121, 3381]

_ENGLISH_CUES = [
    (2700.0, "and that is why we kept the team small"),
    (3300.0, "the market always rewards patience over speed"),
]
_PATIENCE = "the market always rewards patience over speed"  # said at 3300

_HEBREW_CUES = [
    (2596.760, "אתם יכולים לקרוא לזה כסף שנופל מהשמיים"),
    (2641.119, "הכולל של ביטקוין ברשת יהיה 21 מיליון ולא"),
]

# One stamp 10 minutes late between neighbours 100 s apart (the reviewer's probe).
_ONE_LATE = (2500, 2600, 3300, 2700, 2800)


def _index(cues: list[tuple[float, str]], language: str) -> QuoteIndex:
    return QuoteIndex.build([{"text": t, "startMs": int(s * 1000)} for s, t in cues], language)


def _anchored(data: dict[str, Any], index: QuoteIndex | None = None) -> dict[str, Any]:
    return anchor_stamps(data, _CHUNK_2, index, StampReport())


def _claims(*stamps: int, quote: str | None = None) -> dict[str, Any]:
    claims = [{"claim": f"claim at {s}", "quote": quote, "timestamp": s} for s in stamps]
    return {"learning": {"claims": claims}}


def _moments(*stamps: int) -> list[dict[str, Any]]:
    return [{"time": "", "seconds": s, "label": f"m{s}", "description": f"at {s}"} for s in stamps]


def _segments(*stamps: int) -> list[dict[str, Any]]:
    return [{"title": f"s{s}", "summary": f"segment at {s}", "timestamp": s} for s in stamps]


def _stamps(items: list[dict[str, Any]], key: str = "timestamp") -> list[Any]:
    return [item.get(key) for item in items]


# ─── A-C1: one-sided ───


class TestOneSidedOrderGuard:
    def test_should_keep_a_closing_items_earlier_stamp_when_it_is_listed_after_later_ones(
        self,
    ) -> None:
        anchored = _anchored(_claims(*_S15_CLAIMS))

        assert _stamps(anchored["learning"]["claims"]) == _S15_CLAIMS

    def test_should_keep_an_early_stamp_when_its_bracket_is_wider_than_300_seconds(
        self,
    ) -> None:
        data = _claims(2500, 2600, 2700, 2800, 2410)

        assert _anchored(data) is data

    def test_should_keep_a_moment_stamped_early_when_it_is_listed_last(self) -> None:
        moments = _moments(2500, 2600, 2700, 2800, 2410)

        anchored = _anchored({"learning": {"timestamps": moments}})

        assert _stamps(anchored["learning"]["timestamps"], "seconds") == [
            2500,
            2600,
            2700,
            2800,
            2410,
        ]

    def test_should_still_pull_back_a_stamp_when_it_is_over_300_seconds_late(self) -> None:
        anchored = _anchored(_claims(2500, 2600, 3300, 2700, 2800))

        assert _stamps(anchored["learning"]["claims"]) == [2500, 2600, 2600, 2700, 2800]

    def test_should_keep_a_late_items_stamp_when_its_verified_quote_is_said_there(
        self,
    ) -> None:
        claims = _claims(2450, 2500, 2550, 2600)["learning"]["claims"]
        claims.insert(2, {"claim": "patience", "quote": _PATIENCE, "timestamp": 3300})
        data = {"learning": {"claims": claims}}

        assert _anchored(data, _index(_ENGLISH_CUES, "en")) is data


# ─── A-W1: no timeline item lost to its neighbour's second ───


class TestTimelineItemsKeepTheirOwnSecond:
    def test_should_ship_every_moment_when_one_is_pulled_back_onto_its_neighbours_second(
        self,
    ) -> None:
        anchored = _anchored({"learning": {"timestamps": _moments(*_ONE_LATE)}})

        shipped = normalize_moment_items(anchored["learning"]["timestamps"])

        assert [m["seconds"] for m in shipped] == [2500, 2600, 2601, 2700, 2800]

    def test_should_ship_every_segment_when_one_is_pulled_back_onto_its_neighbours_second(
        self,
    ) -> None:
        anchored = _anchored({"podcast": {"segments": _segments(*_ONE_LATE)}})

        assert len(normalize_moment_items(anchored["podcast"]["segments"])) == len(_ONE_LATE)

    def test_should_give_two_pulled_back_moments_two_seconds_when_they_share_a_bracket(
        self,
    ) -> None:
        moments = _moments(2500, 2600, 3300, 3350, 2700, 2800, 2900)

        anchored = _anchored({"learning": {"timestamps": moments}})

        assert _stamps(anchored["learning"]["timestamps"], "seconds")[2:4] == [2601, 2602]

    def test_should_rewrite_a_pulled_back_moments_clock_when_it_takes_a_free_second(
        self,
    ) -> None:
        anchored = _anchored({"learning": {"timestamps": _moments(*_ONE_LATE)}})

        assert anchored["learning"]["timestamps"][2]["time"] == "43:21"

    def test_should_ship_a_translated_qa_row_listed_out_of_order_when_the_window_is_closed(
        self,
    ) -> None:
        rows = [
            {"question": f"What happened at minute {s // 60}?", "answer": "a", "timestamp": s}
            for s in _ONE_LATE
        ]
        anchored = _anchored({"podcast": {"qa": rows}})

        checked, _ = check_questions(anchored, _index(_HEBREW_CUES, "he"))

        assert len(normalize_moment_items((checked or {})["podcast"]["qa"])) == len(rows)

    def test_should_drop_a_segment_whose_late_stamp_is_cleared_when_its_bracket_is_wide(
        self,
    ) -> None:
        anchored = _anchored({"podcast": {"segments": _segments(2500, 4200, 2900, 3200)}})

        assert _stamps(anchored["podcast"]["segments"]) == [2500, 2900, 3200]

    def test_should_count_a_moment_the_guard_drops_as_dropped_when_its_bracket_is_wide(
        self,
    ) -> None:
        report = StampReport()
        data = {"learning": {"timestamps": _moments(2500, 4200, 2900, 3200)}}

        anchor_stamps(data, _CHUNK_2, None, report)

        counts = report.counts["learning.timestamps"]
        assert (counts["dropped"], counts.get("reordered", 0)) == (1, 0)


# ─── A-W4: interview paths switch podcast on only on an interview run ───

_QA_TAB = {"id": "qa", "component": "moment_track", "dataSource": "podcast.qa"}
_GUESTS_TAB = {"id": "guests", "component": "spot_explorer", "dataSource": "podcast.guests"}
_POSITIONS = {"id": "positions", "component": "spot_explorer", "dataSource": "learning.keyIdeas"}


class TestInterviewPathsNeedAnInterviewRun:
    @pytest.mark.parametrize("content_format", ["talk", "lecture"])
    def test_should_not_switch_podcast_on_when_a_talk_plan_names_its_audience_qa(
        self, content_format: str
    ) -> None:
        assert activate_argument_domains([_QA_TAB], ["learning"], content_format) == ["learning"]

    def test_should_not_switch_podcast_on_when_a_talk_plan_names_the_guests(self) -> None:
        assert activate_argument_domains([_GUESTS_TAB], ["learning"], "talk") == ["learning"]

    @pytest.mark.parametrize("content_format", ["interview", "podcast"])
    def test_should_switch_podcast_on_when_an_interview_plan_names_the_qa(
        self, content_format: str
    ) -> None:
        tags = activate_argument_domains([_QA_TAB], ["learning"], content_format)

        assert tags == ["learning", "podcast"]

    def test_should_switch_podcast_on_when_the_probe_gave_no_format(self) -> None:
        assert activate_argument_domains([_QA_TAB], ["learning"], None) == ["learning", "podcast"]

    def test_should_still_switch_learning_on_when_a_talk_plan_names_key_ideas(self) -> None:
        tags = activate_argument_domains([_POSITIONS], ["podcast"], "talk")

        assert tags == ["podcast", "learning"]

    def test_should_repoint_a_talks_qa_tab_to_learning_timestamps_when_the_run_is_a_talk(
        self,
    ) -> None:
        tags = activate_argument_domains([_QA_TAB], ["learning"], "talk")

        kept, _, _ = reconcile_plan_tabs([_QA_TAB], tags, [])

        assert kept[0]["dataSource"] == "learning.timestamps"

    def test_should_keep_the_podcast_tag_off_when_the_plan_of_a_talk_run_names_its_audience_qa(
        self,
    ) -> None:
        qa = {**_QA_TAB, "label": "Audience Q&A", "goal": "After this you know what was asked"}
        data = {"contentTags": ["learning"], "primaryTag": "learning", "tabs": [qa]}

        _normalize_plan_data(data, "talk")

        assert data["contentTags"] == ["learning"]


# ─── A-W3: the probe's interview line ───


def _probe_guidance() -> str:
    template = tier_probe.PROMPT_PATH.read_text(encoding="utf-8")
    return template.split("<classification_guidance>")[1].split("</classification_guidance>")[0]


class TestProbeInterviewLine:
    def test_should_keep_a_solo_talk_with_end_questions_a_talk_when_a_host_introduces_it(
        self,
    ) -> None:
        line = next(line for line in _probe_guidance().splitlines() if "throughout" in line)

        assert 'presents alone and takes questions only at the end stays "talk"' in line

    @pytest.mark.parametrize("series", ["Talks at Google", "book events", "firesides"])
    def test_should_name_no_series_when_it_tells_an_interview_from_a_talk(
        self, series: str
    ) -> None:
        assert series not in _probe_guidance()
