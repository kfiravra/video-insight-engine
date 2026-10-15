"""0b.11 — stat_banner builder (C8): numeric key points → value + meaning + timestamp.

The answer tab (first content tab) carries a bottom numbers strip when at
least _STAT_BANNER_MIN key points hold a figure and a timestamp. Each tile
matches the FE contract — ``label`` + ``value`` strings (tab-prop-schemas.ts
``statBannerSchema``, StatBanner.tsx ``StatBannerStat``) — and carries
``meaning`` + ``timestamp`` through the schema's passthrough.
"""

from __future__ import annotations

import pytest

from src.services.pipeline.assembly.attachments import (
    _STAT_BANNER_MAX,
    _STAT_BANNER_MIN,
    _STAT_LABEL_MAX_CHARS,
    _stat_value,
    attach_response_secondaries,
    attach_secondaries,
)

_TITLES = [
    "150,000 lines in one month vs ~30,000 per year",
    "Ruby fell from over half of his work to about 3%",
    "6 native apps kicked off in about a week",
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
            ("6 native apps kicked off in about a week", "6"),
            ("10 percent better", "10 percent"),
        ],
    )
    def test_headline_figure(self, title: str, expected: str):
        assert _stat_value(title) == expected

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
            "label": "150,000 lines in one month vs ~30,000 per year",
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
    def test_only_the_first_content_tab_carries_the_strip(self):
        overview = {"id": "overview", "component": "overview", "goal": "g", "props": {"data": {}}}
        tabs = [overview, dict(_answer_tab(), id="first"), dict(_answer_tab(), id="second")]

        attach_response_secondaries(tabs, _extraction(8), None, None, "learning")

        carriers = [t["id"] for t in tabs if _banner(t.get("attachments", []))]
        assert carriers == ["first"]
