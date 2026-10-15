"""Shared text helpers for assemblers.

Raw ``s[:N]`` slices cut mid-word with no ellipsis ("Rare, high-cost powe"),
which reads as a rendering bug rather than a truncation. Every user-visible
string cap in assembly goes through :func:`truncate_words` instead.
"""

from __future__ import annotations

import unicodedata

_ZWJ = "\u200d"
# Emoji skin-tone modifiers and the tag characters of subdivision flags: not
# marks, yet each belongs to the emoji before it.
_EMOJI_MODIFIERS = ("\U0001f3fb", "\U0001f3ff")
_EMOJI_TAGS = ("\U000e0020", "\U000e007f")
_REGIONAL_INDICATORS = ("\U0001f1e6", "\U0001f1ff")


def _within(ch: str, bounds: tuple[str, str]) -> bool:
    return bounds[0] <= ch <= bounds[1]


def _second_flag_letter(text: str, index: int) -> bool:
    """Whether ``text[index]`` is the second regional indicator of a flag pair."""
    if not _within(text[index], _REGIONAL_INDICATORS):
        return False
    run = 0
    while index - run - 1 >= 0 and _within(text[index - run - 1], _REGIONAL_INDICATORS):
        run += 1
    return run % 2 == 1


def _joins_previous(text: str, index: int) -> bool:
    """Whether ``text[index]`` continues the grapheme of the character before it:
    a mark (combining, spacing or enclosing — ``unicodedata.combining`` misses the
    last two and Thai vowels), a ZWJ or the character a ZWJ glues on, an emoji
    modifier or tag, or a flag's second letter."""
    ch = text[index]
    return (
        unicodedata.combining(ch) > 0
        or unicodedata.category(ch).startswith("M")
        or ch == _ZWJ
        or text[index - 1] == _ZWJ
        or _within(ch, _EMOJI_MODIFIERS)
        or _within(ch, _EMOJI_TAGS)
        or _second_flag_letter(text, index)
    )


def _grapheme_start(text: str, index: int) -> int:
    """``index`` moved back to the start of the grapheme cluster it falls in, so a
    cut there never strands a mark or splits an emoji sequence."""
    while 0 < index < len(text) and _joins_previous(text, index):
        index -= 1
    return index


def truncate_words(text: str, cap: int) -> str:
    """Truncate to at most ``cap`` chars on a word boundary, with an ellipsis.

    Breaks at the last space before the cap when one exists in the final
    two-thirds of the budget (avoids "a…" degenerate cuts for long words);
    otherwise falls back to a hard cut, moved back to a grapheme boundary
    (unspaced scripts — Thai, CJK — and emoji runs take this path). Returns
    short strings unchanged.
    """
    if cap <= 0 or len(text) <= cap:
        return text
    head = text[: cap - 1]
    space = head.rfind(" ")
    if space >= (cap - 1) * 2 // 3:
        head = head[:space]
    else:
        head = text[: _grapheme_start(text, cap - 1)]
    return head.rstrip() + "…"
