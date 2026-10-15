"""Assembly ``truncate_words`` (0b review): the word-boundary cut is unchanged; the
hard-cut fallback (no space late enough — unspaced scripts, emoji runs) now lands on
a grapheme boundary: never a stranded combining mark, never inside a ZWJ sequence,
a skin-tone pair, a keycap or a flag.
"""

from __future__ import annotations

import pytest

from src.services.pipeline.assembly.text_utils import truncate_words

_E_ACUTE = "é"
_ZWJ = "‍"
_FAMILY = f"\U0001f468{_ZWJ}\U0001f469{_ZWJ}\U0001f467"
_THUMBS_UP_MEDIUM = "\U0001f44d\U0001f3fd"
_FLAG_JP = "\U0001f1ef\U0001f1f5"
_KEYCAP_ONE = "1️⃣"
_THAI_KA_MAI_HAN_AKAT = "กั"


class TestWordBoundary:
    def test_should_return_short_text_unchanged(self):
        assert truncate_words("short", 10) == "short"

    def test_should_return_the_text_for_a_non_positive_cap(self):
        assert truncate_words("anything at all", 0) == "anything at all"

    def test_should_cut_at_the_last_space_with_an_ellipsis(self):
        assert truncate_words("Rare, high-cost power tools for the workshop", 20) == (
            "Rare, high-cost…"
        )

    def test_should_hard_cut_a_long_word_as_before(self):
        assert truncate_words("x" * 30, 10) == "x" * 9 + "…"

    def test_should_hard_cut_when_the_last_space_is_too_early(self):
        assert truncate_words("a " + "b" * 30, 10) == "a " + "b" * 7 + "…"


class TestGraphemeSafeHardCut:
    def test_should_not_strand_a_combining_mark(self):
        assert truncate_words(_E_ACUTE * 10, 6) == _E_ACUTE * 2 + "…"

    def test_should_not_split_a_zwj_sequence(self):
        assert truncate_words(_FAMILY * 4, 7) == _FAMILY + "…"

    def test_should_keep_a_skin_tone_with_its_emoji(self):
        assert truncate_words(_THUMBS_UP_MEDIUM * 6, 6) == _THUMBS_UP_MEDIUM * 2 + "…"

    def test_should_not_split_a_flag(self):
        assert truncate_words(_FLAG_JP * 5, 6) == _FLAG_JP * 2 + "…"

    def test_should_keep_a_keycap_whole(self):
        assert truncate_words(_KEYCAP_ONE * 5, 6) == _KEYCAP_ONE + "…"

    def test_should_keep_a_thai_vowel_mark_on_its_consonant(self):
        assert truncate_words(_THAI_KA_MAI_HAN_AKAT * 10, 6) == _THAI_KA_MAI_HAN_AKAT * 2 + "…"

    def test_should_give_only_the_ellipsis_for_one_cluster_longer_than_the_cap(self):
        assert truncate_words("a" + "́" * 50, 10) == "…"

    @pytest.mark.parametrize(
        "text",
        [_E_ACUTE * 40, _FAMILY * 10, _THUMBS_UP_MEDIUM * 20, _FLAG_JP * 20, _KEYCAP_ONE * 20],
    )
    @pytest.mark.parametrize("cap", [5, 6, 7, 8, 11])
    def test_should_stay_within_the_cap(self, text: str, cap: int):
        assert len(truncate_words(text, cap)) <= cap
