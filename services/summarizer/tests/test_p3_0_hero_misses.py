"""The gate-2 hero misses (output-quality 3.0; ``gates/p3.0-hero-misses.md``).

Stamp source — a takeaway is stamped where its cited item's quote is said, so the job asks
for that quote's claim, from one item, never from a line without an id (S06, S07, S09,
S10); S02's claim and quote sat 184 s into the answer, past the old 180 s move cap · wrong
stance and paraphrase — a possibility stays one, no link the video does not draw, "each" /
"every" / "all" only for every one, and no line example that models "each" (S02, S03,
S09, S10) · the cache — the job rides in block 3, so blocks 1 / 2 never carry it.
"""

from __future__ import annotations

import re
from typing import Any

from src.services.pipeline.hero_compact import compact_for_hero, stamps_at_quotes
from src.services.pipeline.hero_job import GroupBlocks, HeroRequest, cached_blocks, job_text
from src.services.pipeline.quote_verify import QuoteIndex
from tests.test_p2_1_hero_rewrite import _CHUNK, _EXTRACTION, _prompt

# S02: the question opens at 148 s; the answer's claim and quote come minutes later.
_ANSWERED: dict[str, Any] = {
    "podcast": {
        "qa": [
            {
                "question": "Is there intelligent life out there?",
                "answer": "Probably not: we are the only ones building advanced tech.",
                "quote": "let's be really grateful for this amazing opportunity",
                "timestamp": 148,
            }
        ]
    }
}
_SAID = (
    "I think it's much more prudent to say let's be really grateful for this amazing opportunity"
)


def _said_after(seconds: int) -> QuoteIndex:
    return QuoteIndex.build(
        [
            {"text": "Do you think there's intelligent life out there?", "start": 129},
            {"text": _SAID, "start": 148 + seconds},
        ]
    )


def _job() -> str:
    request = HeroRequest(title="Alice on agents", duration=600, extraction=_EXTRACTION)
    return job_text(request, compact_for_hero(_EXTRACTION, 600))


# ─── Stamp source: the move cap ───


class TestStampMoveCap:
    def test_should_move_the_stamp_when_the_quote_is_said_184_s_into_the_answer(self) -> None:
        compact = compact_for_hero(_ANSWERED, 3600)

        assert stamps_at_quotes(compact, _said_after(184)) == {"Q1": 332}

    def test_should_keep_the_items_stamp_when_the_quote_is_said_over_240_s_away(self) -> None:
        compact = compact_for_hero(_ANSWERED, 3600)

        assert stamps_at_quotes(compact, _said_after(241)) == {"Q1": 148}


# ─── Stamp source: what the job asks ───


class TestStampSourceRules:
    def test_should_ask_for_the_claim_the_cited_items_quote_makes(self) -> None:
        assert "the takeaway makes the claim the quote makes" in _job()

    def test_should_forbid_one_takeaway_over_two_items(self) -> None:
        assert 'never "X and Y each …" over two items' in _job()

    def test_should_forbid_a_ref_for_what_a_line_without_an_id_says(self) -> None:
        assert '("- …" lines have no stamp)' in _job()

    def test_should_ask_to_shorten_without_fusing_two_steps(self) -> None:
        assert "never by fusing two steps or claims into one" in _job()


# ─── Wrong stance and paraphrase ───


class TestStanceRules:
    def test_should_keep_a_possibility_from_becoming_the_videos_finding(self) -> None:
        assert "never written as what the video found or proved" in _job()

    def test_should_join_two_claims_only_where_the_video_draws_the_link(self) -> None:
        assert '"which means" only where the video draws that link' in _job()

    def test_should_write_each_every_or_all_only_for_every_one(self) -> None:
        assert 'write "each", "every" or "all" only where it gives that for every one' in _job()

    def test_should_draw_no_line_example_with_each_every_or_all(self) -> None:
        examples = re.findall(r'From another video: "([^"]+)"', _job())
        words = {word.strip(",.:;").casefold() for example in examples for word in example.split()}

        assert words & {"each", "every", "all"} == set()


# ─── The cache ───


class TestCache:
    def test_should_keep_the_hero_job_out_of_the_cached_blocks(self) -> None:
        request = HeroRequest(
            title="Alice on agents",
            duration=600,
            extraction=_EXTRACTION,
            blocks=GroupBlocks(_prompt(), (_CHUNK,)),
        )

        assert [b for b in cached_blocks(request) if "Stance: as the video" in b["text"]] == []
