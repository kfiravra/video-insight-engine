"""0b.11 — stat_banner builder (C8): numeric key points → value + meaning + timestamp.

The answer tab (first content tab) carries a bottom numbers strip when at
least _STAT_BANNER_MIN key points hold a figure and a timestamp. Each tile
matches the FE contract — ``label`` + ``value`` strings (tab-prop-schemas.ts
``statBannerSchema``, StatBanner.tsx ``StatBannerStat``) — and carries
``meaning`` + ``timestamp`` through the schema's passthrough. The label is a
short phrase from the title (the full title is the ``meaning``); a bare year or
a bare single digit ("Rails 8", "in 2023") is a name, not a figure.
"""

from __future__ import annotations

import pytest

from src.services.pipeline.assembly.attachments import (
    _STAT_BANNER_MAX,
    _STAT_BANNER_MIN,
    _STAT_LABEL_MAX_CHARS,
    _STAT_LABEL_MAX_WORDS,
    _stat_figure,
    _stat_label,
    _stat_value,
    attach_response_secondaries,
    attach_secondaries,
)

_TITLES = [
    "150,000 lines in one month vs ~30,000 per year",
    "Ruby fell from over half of his work to about 3%",
    "Agent evals saturated at 95% completion, so the bar was raised",
    "Rust backend: 99% less CPU and 95% less memory",
    "About $20 million raised for Omarchy",
    "Omarchy install time: 3:33 to 35 seconds to 9 seconds",
    "2 trillion photos a year, up from a trickle",
    "1,000x gap: worst programmer without tools vs best with them",
    "ATMs and Jevons' paradox: 30,000 to 40,000 bank tellers",
]


def _key_points(n: int, timestamp: bool = True) -> list[dict]:
    return [
        {
            "emoji": "📈",
            "title": _TITLES[i % len(_TITLES)],
            "detail": "d",
            **({"timestamp": 60 * (i + 1)} if timestamp else {}),
        }
        for i in range(n)
    ]


def _extraction(n: int = _STAT_BANNER_MIN, **kwargs) -> dict:
    return {"learning": {"keyPoints": _key_points(n, **kwargs)}}


def _answer_tab() -> dict:
    return {
        "id": "key_arguments",
        "component": "concept_canvas",
        "goal": "Explore the ideas.",
        "props": {"concepts": [{"name": f"c{i}"} for i in range(6)]},
    }


def _banner(attachments: list[dict]) -> dict | None:
    return next((a for a in attachments if a["component"] == "stat_banner"), None)


class TestStatValue:
    @pytest.mark.parametrize(
        ("title", "expected"),
        [
            ("150,000 lines in one month vs ~30,000 per year", "150,000 lines"),
            ("Ruby fell from over half of his work to about 3%", "3%"),
            ("About $20 million raised for Omarchy", "$20 million"),
            ("1,000x gap: worst programmer vs best", "1,000x"),
            ("Optimization within reach: 0.5 MB vs Spotify's 1.2 GB", "0.5 MB"),
            ("Omarchy install time: 3:33 to 35 seconds", "3:33"),
            ("10 percent better", "10 percent"),
            ("2 trillion photos a year", "2 trillion"),
            ("3x faster builds", "3x"),
        ],
    )
    def test_headline_figure(self, title: str, expected: str):
        assert _stat_value(title) == expected

    @pytest.mark.parametrize(
        ("title", "expected"),
        [("Revenue hit $5, then doubled", "$5"), ("Grew to 150,000, then stalled", "150,000")],
    )
    def test_should_not_swallow_a_trailing_comma(self, title: str, expected: str):
        assert _stat_value(title) == expected

    @pytest.mark.parametrize(
        ("title", "expected"),
        [
            ("A 0.5MB binary", "0.5MB"),
            ("100ms p99 latency", "100ms"),
            ("50GB of RAM", "50GB"),
            ("Ships in 2days", "2days"),
        ],
    )
    def test_should_read_a_unit_glued_to_its_number(self, title: str, expected: str):
        assert _stat_value(title) == expected

    @pytest.mark.parametrize(
        "title",
        [
            "In 2023, Basecamp left the cloud",
            "Founded in 1999",
            "Rails 8 ships Solid Queue",
            "Python 3 drops the GIL",
            "6 native apps kicked off in about a week",
        ],
    )
    def test_should_not_count_a_bare_year_or_single_digit(self, title: str):
        assert _stat_value(title) is None

    def test_should_skip_a_bare_year_for_a_later_figure(self):
        assert _stat_value("In 2023, revenue grew 40%") == "40%"

    def test_should_keep_a_two_digit_bare_number(self):
        assert _stat_value("Founded in 1999 by 12 people") == "12"

    def test_should_not_read_the_tail_of_a_decimal_comma(self):
        assert _stat_value("1,5 million users") is None

    def test_a_figure_with_a_unit_beats_an_earlier_bare_number(self):
        assert _stat_value("Basecamp 5 got 99% faster") == "99%"

    def test_a_multi_digit_number_beats_an_earlier_single_digit(self):
        assert _stat_value("Basecamp 5: 20 or 30 vibe-coded PRs") == "20"

    @pytest.mark.parametrize(
        "title",
        ["Choose P-bloom over P-doom", "Panic in the 1950s", "React v2.5 and x86 chips"],
    )
    def test_no_figure_yields_none(self, title: str):
        assert _stat_value(title) is None


def _label(title: str) -> str:
    figure = _stat_figure(title)
    assert figure is not None
    return _stat_label(title, figure)


class TestStatLabel:
    @pytest.mark.parametrize(
        ("title", "expected"),
        [
            ("Rust backend: 99% less CPU and 95% less memory", "Rust backend"),
            ("Omarchy install time: 3:33 to 35 seconds to 9 seconds", "Omarchy install time"),
            ("Half as much code in 20 months as in the previous 21 years", "Half as much code"),
            (
                "Agent evals saturated at 95% completion, so the bar was raised",
                "Agent evals saturated",
            ),
        ],
    )
    def test_should_label_with_the_clause_before_the_figure(self, title: str, expected: str):
        assert _label(title) == expected

    @pytest.mark.parametrize(
        ("title", "expected"),
        [
            ("150,000 lines in one month vs ~30,000 per year", "in one month"),
            ("2 trillion photos a year, up from a trickle", "photos a year"),
            ("1,000x gap: worst programmer without tools vs best with them", "gap"),
        ],
    )
    def test_should_label_with_the_clause_after_a_leading_figure(self, title: str, expected: str):
        assert _label(title) == expected

    def test_should_skip_a_lone_hedge_before_the_figure(self):
        assert _label("About $20 million raised for Omarchy") == "raised for Omarchy"

    def test_should_trim_dangling_connectives(self):
        assert _label("Ruby fell from over half of his work to about 3%") == "Ruby fell"

    def test_should_cap_the_label_in_words(self):
        title = "Every single one of the many enterprise teams surveyed: 40% faster"

        assert len(_label(title).split()) <= _STAT_LABEL_MAX_WORDS

    def test_should_fall_back_to_the_title_when_no_word_is_left(self):
        assert _label("99%") == "99%"


class TestBuilder:
    def test_answer_tab_gets_a_bottom_strip(self):
        banner = _banner(
            attach_secondaries(
                _answer_tab(), _extraction(), None, None, "learning", is_answer_tab=True
            )
        )

        assert banner is not None
        assert (banner["slot"], banner["size"]) == ("bottom", "strip")

    def test_tile_matches_the_fe_contract(self):
        banner = _banner(
            attach_secondaries(
                _answer_tab(), _extraction(), None, None, "learning", is_answer_tab=True
            )
        )

        assert banner is not None
        tile = banner["props"]["stats"][0]
        assert tile == {
            "label": "in one month",
            "value": "150,000 lines",
            "emoji": "📈",
            "meaning": "150,000 lines in one month vs ~30,000 per year",
            "timestamp": 60,
        }

    def test_label_and_value_are_strings(self):
        banner = _banner(
            attach_secondaries(
                _answer_tab(), _extraction(), None, None, "learning", is_answer_tab=True
            )
        )

        assert banner is not None
        assert all(
            isinstance(t["label"], str) and isinstance(t["value"], str)
            for t in banner["props"]["stats"]
        )

    def test_every_tile_carries_a_timestamp(self):
        banner = _banner(
            attach_secondaries(
                _answer_tab(), _extraction(8), None, None, "learning", is_answer_tab=True
            )
        )

        assert banner is not None
        assert all(isinstance(t["timestamp"], int) for t in banner["props"]["stats"])

    def test_long_title_is_capped_in_the_label_but_whole_in_meaning(self):
        long_title = "99% of the " + "very " * 20 + "long sentence"
        extraction = {"learning": {"keyPoints": _key_points(_STAT_BANNER_MIN)}}
        extraction["learning"]["keyPoints"][0]["title"] = long_title

        banner = _banner(
            attach_secondaries(
                _answer_tab(), extraction, None, None, "learning", is_answer_tab=True
            )
        )

        assert banner is not None
        tile = banner["props"]["stats"][0]
        assert len(tile["label"]) <= _STAT_LABEL_MAX_CHARS
        assert tile["meaning"] == long_title

    def test_strip_is_capped(self):
        banner = _banner(
            attach_secondaries(
                _answer_tab(),
                _extraction(_STAT_BANNER_MAX + 4),
                None,
                None,
                "learning",
                is_answer_tab=True,
            )
        )

        assert banner is not None
        assert len(banner["props"]["stats"]) == _STAT_BANNER_MAX

    def test_answer_tab_without_a_primary_list_still_gets_the_strip(self):
        filmstrip = {"id": "frames", "component": "video_filmstrip", "props": {"frames": []}}

        banner = _banner(
            attach_secondaries(filmstrip, _extraction(), None, None, "learning", is_answer_tab=True)
        )

        assert banner is not None

    def test_key_points_from_a_secondary_domain_are_used(self):
        extraction = {"tech": {"topics": []}, **_extraction()}

        banner = _banner(
            attach_secondaries(_answer_tab(), extraction, None, None, "tech", is_answer_tab=True)
        )

        assert banner is not None


class TestGates:
    def test_below_minimum_yields_no_banner(self):
        out = attach_secondaries(
            _answer_tab(),
            _extraction(_STAT_BANNER_MIN - 1),
            None,
            None,
            "learning",
            is_answer_tab=True,
        )

        assert _banner(out) is None

    def test_key_points_without_timestamps_do_not_count(self):
        out = attach_secondaries(
            _answer_tab(),
            _extraction(8, timestamp=False),
            None,
            None,
            "learning",
            is_answer_tab=True,
        )

        assert _banner(out) is None

    def test_key_points_without_a_figure_do_not_count(self):
        extraction = {
            "learning": {
                "keyPoints": [
                    {"title": "Choose P-bloom over P-doom", "timestamp": 10 * i} for i in range(8)
                ]
            }
        }

        out = attach_secondaries(
            _answer_tab(), extraction, None, None, "learning", is_answer_tab=True
        )

        assert _banner(out) is None

    def test_non_answer_tab_never_gets_a_banner(self):
        out = attach_secondaries(_answer_tab(), _extraction(8), None, None, "learning")

        assert _banner(out) is None

    def test_banner_coexists_with_the_dense_summary_header(self):
        tab = _answer_tab()
        tab["props"]["concepts"] = [{"name": f"c{i}", "definition": "d"} for i in range(12)]

        out = attach_secondaries(tab, _extraction(), None, None, "learning", is_answer_tab=True)

        assert [a["component"] for a in out] == ["summary_header", "stat_banner"]


class TestResponseLoop:
    def test_a_no_list_answer_tab_keeps_the_strip_for_itself(self):
        overview = {"id": "overview", "component": "overview", "goal": "g", "props": {"data": {}}}
        filmstrip = {"id": "frames", "component": "video_filmstrip", "props": {"frames": []}}
        tabs = [overview, filmstrip, dict(_answer_tab(), id="second")]

        attach_response_secondaries(tabs, _extraction(8), None, None, "learning")

        carriers = [t["id"] for t in tabs if _banner(t.get("attachments", []))]
        assert carriers == ["frames"]

    def test_only_the_first_content_tab_carries_the_strip(self):
        overview = {"id": "overview", "component": "overview", "goal": "g", "props": {"data": {}}}
        tabs = [overview, dict(_answer_tab(), id="first"), dict(_answer_tab(), id="second")]

        attach_response_secondaries(tabs, _extraction(8), None, None, "learning")

        carriers = [t["id"] for t in tabs if _banner(t.get("attachments", []))]
        assert carriers == ["first"]
