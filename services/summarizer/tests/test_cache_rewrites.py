"""Tests for cache-rewrite counting (pipeline/cache_rewrites.py) — hotfix 4.1 (3)."""

from __future__ import annotations

from src.services.pipeline.cache_rewrites import rewrite_counts


def _text1(chunk: str = "", *, ttft: int = 4000, w1h: int = 0, w: int = 9886) -> dict:
    return {
        "span": f"extraction_text1{chunk}",
        "startMs": 0,
        "ttftMs": ttft,
        "cacheWriteTokens": w,
        "cacheWrite1hTokens": w1h,
    }


def _group(span: str, start: int, written: int, **extra: object) -> dict:
    return {"span": span, "startMs": start, "cacheWriteTokens": written, **extra}


def test_should_count_a_group_released_before_text1s_first_token_that_wrote():
    """jMq8 r1 on prod: text1's first token at 3.96 s, text2/3 released at 1.5 s."""
    counts = rewrite_counts(
        [_text1(), _group("extraction_text2", 1500, 9886), _group("extraction_text3", 1500, 9886)]
    )

    assert counts == {"cacheRewrites": 2, "cacheRewriteTokens": 19772}


def test_should_not_count_groups_that_read_the_prefix():
    counts = rewrite_counts([_text1(), _group("extraction_text2", 4000, 0, cacheReadTokens=9886)])

    assert counts["cacheRewrites"] == 0


def test_should_not_count_a_write_after_text1s_first_token():
    """A lapsed 5-minute TTL (the visual group at frames-done), not a race."""
    counts = rewrite_counts([_text1(), _group("extraction_visual", 90000, 5300)])

    assert counts["cacheRewrites"] == 0


def test_should_not_count_the_only_writer_of_a_chunk_without_text1():
    counts = rewrite_counts([_group("extraction_visual", 0, 9886)])

    assert counts["cacheRewrites"] == 0


def test_should_not_count_a_refusal_re_send_on_the_previous_model():
    counts = rewrite_counts(
        [
            _text1(),
            _group("extraction_text2", 1500, 9886, refusedModel="anthropic/claude-sonnet-5-5"),
        ]
    )

    assert counts["cacheRewrites"] == 0


def test_should_count_a_later_chunks_text1_writing_the_shared_1h_block():
    counts = rewrite_counts([_text1(w1h=4587), _text1("_c1", w1h=4587, w=9886)])

    assert counts == {"cacheRewrites": 1, "cacheRewriteTokens": 4587}


def test_should_judge_each_chunk_by_its_own_text1():
    counts = rewrite_counts(
        [
            _text1(ttft=1000),
            _text1("_c1", ttft=6000),
            _group("extraction_text2", 1500, 9886),
            _group("extraction_text2_c1", 1500, 9886),
        ]
    )

    assert counts == {"cacheRewrites": 1, "cacheRewriteTokens": 9886}


def test_should_ignore_calls_outside_the_split_extraction():
    counts = rewrite_counts(
        [
            _group("plan", 0, 22070),
            _group("extraction", 0, 8000),
            _group("extraction_batch2", 0, 10),
        ]
    )

    assert counts["cacheRewrites"] == 0
