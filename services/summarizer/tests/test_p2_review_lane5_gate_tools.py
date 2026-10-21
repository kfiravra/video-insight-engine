"""p2 review fixes, lane 5 — the gate-2 hero check (review-p2-D.md D1, D2, D10).

The tools live in ``dev/active/output-quality/tools`` (stamp check: ``hero_locate`` /
``hero_stamps``; rows and buckets: ``hero_check`` / ``hero_report``; the moved-marker
self-test: ``hero_selftest``). Transcripts here are synthetic: 40 s blocks of filler with one
passage said in block 4 (160-200 s, words 30-48 → 175-184 s), the shape of manual captions
whose two-block windows are 80 s wide.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

import pytest

TOOLS = Path(__file__).resolve().parents[3] / "dev" / "active" / "output-quality" / "tools"
if not TOOLS.is_dir():
    pytest.skip("the output-quality gate tools are archived", allow_module_level=True)
sys.path.insert(0, str(TOOLS))

import build_packets  # noqa: E402
import hero_check  # noqa: E402
import hero_report  # noqa: E402
import hero_selftest  # noqa: E402
import hero_stamps  # noqa: E402
from hero_locate import Transcript  # noqa: E402

BLOCK_S = 40
BLOCKS = 30
WORDS_PER_BLOCK = 80
PASSAGE = (
    "we reached the harvest festival where the old market was filled with lantern light "
    "and loud drums"
)
CLAIM = "The harvest festival fills the old market with lantern light and loud drums"
FERRY = "the ferry crossed the harbour while fishermen hauled nets and gulls circled the pier"
FERRY_CLAIM = "Fishermen haul nets from the ferry harbour while gulls circle the pier"
BLOCK_OF_PASSAGE = 4


def _filler(block: int) -> list[str]:
    return [f"filler{(block * 7 + j) % 97}" for j in range(WORDS_PER_BLOCK)]


def _write_transcript(path: Path, said: dict[int, str]) -> Path:
    """``[m:ss] text`` lines: every block filler, a passage replacing words 30+ of its block."""
    lines = []
    for block in range(BLOCKS):
        words = _filler(block)
        if block in said:
            spoken = said[block].split()
            words[30 : 30 + len(spoken)] = spoken
        start = block * BLOCK_S
        lines.append(f"[{start // 60}:{start % 60:02d}] {' '.join(words)}")
    path.write_text("\n".join(lines) + "\n")
    return path


@pytest.fixture
def transcript(tmp_path: Path) -> Transcript:
    return Transcript(_write_transcript(tmp_path / "t.txt", {BLOCK_OF_PASSAGE: PASSAGE}), None)


def _item(stamp: float, quote: str, text: str) -> dict[str, Any]:
    return {"title": text, "timestamp": stamp, "quote": quote}


def _quote_second(tx: Transcript, quote: str, near: int) -> int:
    """The second the pipeline stamps a verified quote at (``hero_compact.stamps_at_quotes``)."""
    seconds = tx.index.verify(quote, near).seconds
    assert seconds is not None
    return seconds


def _placed(tx: Transcript, *items: dict[str, Any]) -> list[hero_stamps.PlacedItem]:
    return hero_stamps.place_items(tx, hero_stamps.quoted_stamped_items({"items": list(items)}))


# ─── D1: the offset is measured from the claim's peak ───


class TestLocateText:
    def test_should_locate_the_claim_where_its_words_are_said_not_over_the_window(
        self, transcript: Transcript
    ) -> None:
        located = transcript.locate_text(CLAIM, 178)

        assert located is not None and located.start >= 170 and located.end <= 190

    @pytest.mark.parametrize("marker", [100, 235, 260])
    def test_should_score_off_when_the_marker_is_far_from_the_claim_inside_its_window(
        self, transcript: Transcript, marker: int
    ) -> None:
        # [120, 200] and [160, 240] hold markers 100..235 as pair windows; the words are at 175-184
        verdict = hero_stamps.check_claim(transcript, CLAIM, marker, [])["verdict"]

        assert verdict == "off"

    def test_should_measure_from_the_peak_when_the_window_edge_is_near(
        self, transcript: Transcript
    ) -> None:
        # marker 140 is 20 s before block 4 starts (the old window-edge gap) but 35 s from the words
        row = hero_stamps.check_claim(transcript, CLAIM, 140, [])

        assert (row["verdict"], row["offset"]) == ("off", pytest.approx(-35, abs=3))

    def test_should_score_ok_when_the_marker_sits_at_the_claim(
        self, transcript: Transcript
    ) -> None:
        assert hero_stamps.check_claim(transcript, CLAIM, 170, [])["verdict"] == "ok"

    def test_should_not_call_a_partial_recap_at_the_marker_the_claim_said_there(
        self, tmp_path: Path
    ) -> None:
        recap = "the harvest festival and the old market again"  # 4 of 9 content words
        tx = Transcript(_write_transcript(tmp_path / "t.txt", {2: recap, 4: PASSAGE}), None)

        # marker inside the recap block, 95 s before the full line: not 'near'
        assert hero_stamps.check_claim(tx, CLAIM, 90, [])["verdict"] == "off"

    def test_should_go_to_the_repeat_nearest_the_marker(self, tmp_path: Path) -> None:
        tx = Transcript(_write_transcript(tmp_path / "t.txt", {4: PASSAGE, 10: PASSAGE}), None)

        assert hero_stamps.check_claim(tx, CLAIM, 410, [])["verdict"] == "ok"

    def test_should_say_nothing_sure_when_the_words_are_not_said(
        self, transcript: Transcript
    ) -> None:
        assert hero_stamps.check_claim(transcript, FERRY_CLAIM, 300, [])["verdict"] == "unlocatable"


# ─── D2: a takeaway matches an item by where the item's quote is said ───


class TestQuoteMatch:
    def test_should_match_the_item_whose_quote_is_said_at_the_marker_not_the_one_stamped_there(
        self, transcript: Transcript
    ) -> None:
        # code moved the stamp from 150 (the model's) to the block where the quote is said (160)
        item = _item(150, PASSAGE, CLAIM)
        marker = _quote_second(transcript, PASSAGE, 150)

        row = hero_stamps.check_claim(transcript, CLAIM, marker, _placed(transcript, item))

        assert (row["method"], row["verdict"]) == ("quote", "ok")

    def test_should_report_the_claims_own_words_said_at_the_marker(
        self, transcript: Transcript
    ) -> None:
        item = _item(150, PASSAGE, CLAIM)

        row = hero_stamps.check_claim(transcript, CLAIM, 160, _placed(transcript, item))

        assert row["claimTextAtMarker"] is True

    def test_should_report_the_claims_words_elsewhere_when_only_the_item_is_at_the_marker(
        self, tmp_path: Path
    ) -> None:
        tx = Transcript(_write_transcript(tmp_path / "t.txt", {4: PASSAGE, 20: FERRY}), None)
        item = _item(
            160, PASSAGE, FERRY_CLAIM
        )  # the item's text is the ferry claim, its quote is not

        row = hero_stamps.check_claim(tx, FERRY_CLAIM, 160, _placed(tx, item))

        assert (row["method"], row["claimTextAtMarker"]) == ("quote", False)

    def test_should_not_let_an_item_sharing_one_word_vouch_for_the_claim(
        self, transcript: Transcript
    ) -> None:
        item = _item(160, "we reached the harvest", "ferry crossing")

        row = hero_stamps.check_claim(transcript, CLAIM, 160, _placed(transcript, item))

        assert row["method"] == "text"

    def test_should_score_a_marker_far_from_its_items_quote_off_by_that_quote(
        self, transcript: Transcript
    ) -> None:
        item = _item(160, PASSAGE, CLAIM)

        row = hero_stamps.check_claim(transcript, CLAIM, 290, _placed(transcript, item))

        assert (row["method"], row["verdict"]) == ("quote", "off")

    def test_should_count_quote_located_takeaways_with_their_words_at_the_marker(
        self, transcript: Transcript
    ) -> None:
        item = _item(160, PASSAGE, CLAIM)
        meta = {"keyTakeaways": [f"{CLAIM} [2:40]"]}

        summary = hero_stamps.takeaway_summary(transcript, meta, {"items": [item]})

        assert (summary["byQuote"], summary["quoteWordsAtMarker"]) == (1, 1)


# ─── D1: the moved-marker self-test ───


class TestSelfTest:
    def test_should_score_every_marker_moved_sixty_seconds_off_the_claim_off(
        self, transcript: Transcript
    ) -> None:
        checks = hero_selftest.shifted_checks(transcript, f"{CLAIM} [2:58]", [], 60, 1200)

        assert [hero_selftest.classify(c) for c in checks] == ["off", "off"]

    def test_should_move_nothing_for_a_takeaway_the_gate_does_not_pass(
        self, transcript: Transcript
    ) -> None:
        assert hero_selftest.shifted_checks(transcript, f"{CLAIM} [9:00]", [], 60, 1200) == []

    def test_should_skip_a_marker_moved_outside_the_video(self, transcript: Transcript) -> None:
        checks = hero_selftest.shifted_checks(transcript, f"{CLAIM} [2:58]", [], 200, 1200)

        assert [c["moved"] for c in checks] == ["after"]

    @pytest.mark.parametrize(
        ("was", "now", "outcome"),
        [([100, 110], [105, 112], "sameSpan"), ([100, 110], [200, 210], "saidAgain")],
    )
    def test_should_tell_an_ok_on_the_old_span_from_the_claim_said_again(
        self, was: list[int], now: list[int], outcome: str
    ) -> None:
        check = {"verdict": "ok", "wasAt": was, "at": now}

        assert hero_selftest.classify(check) == outcome


# ─── D10: both ground truths ───


def _video(
    data: Path, monkeypatch: pytest.MonkeyPatch, caption_type: str, research: dict, creator: dict
) -> None:
    video = data / "S99"
    video.mkdir(parents=True)
    _write_transcript(video / "transcript.txt", research)
    _write_transcript(video / "transcript.pipeline.txt", creator)
    doc = {
        "youtubeId": "x",
        "transcriptMeta": {"type": caption_type},
        "meta": {"keyTakeaways": [f"{CLAIM} [2:55]"]},
        "extraction": {},
    }
    (video / "doc.json").write_text(json.dumps(doc))
    monkeypatch.setattr(hero_check, "DATA", data)
    monkeypatch.setattr(build_packets, "DATA", data)


class TestBuckets:
    def test_should_score_the_manual_captions_video_against_both_ground_truths(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _video(tmp_path, monkeypatch, "manual", {4: PASSAGE}, {20: PASSAGE})

        buckets = hero_check.video_row("S99")["buckets"]

        assert (buckets["research"]["takeaways"]["ok"], buckets["creator"]["takeaways"]["off"]) == (
            1,
            1,
        )

    def test_should_report_no_creator_bucket_for_auto_captions(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _video(tmp_path, monkeypatch, "auto-generated", {4: PASSAGE}, {4: PASSAGE})

        assert hero_check.video_row("S99")["buckets"]["creator"] is None

    def test_should_keep_the_packets_choice_in_the_main_columns(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _video(tmp_path, monkeypatch, "manual", {4: PASSAGE}, {20: PASSAGE})

        row = hero_check.video_row("S99")

        assert (row["transcript"], row["takeaways"]["off"]) == ("pipeline", 1)

    def test_should_print_one_total_line_per_ground_truth(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _video(tmp_path, monkeypatch, "manual", {4: PASSAGE}, {20: PASSAGE})
        total = hero_report.total_row([hero_check.video_row("S99")])

        lines = hero_report.bucket_lines(total, 30)

        assert [line.split(" (")[0] for line in lines] == [
            "vs transcript.txt",
            "vs creator's captions",
        ]

    def test_should_name_the_quote_located_share_with_the_claims_words_in_the_stamp_line(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _video(tmp_path, monkeypatch, "manual", {4: PASSAGE}, {4: PASSAGE})
        total = hero_report.total_row([hero_check.video_row("S99")])

        assert (
            "the claim's own words said at the marker for" in hero_report.gate_lines(total, 30)[0]
        )
