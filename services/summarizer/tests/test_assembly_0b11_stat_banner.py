"""0b.11 — stat_banner builder (C8): stated figures → value + meaning + timestamp.

The answer tab (first content tab) carries a bottom numbers strip when at
least _STAT_BANNER_MIN key ideas carry a figure the model stated (``kind:
number`` with ``figure`` ``{value, label}``) and a timestamp. Each tile matches
the FE contract — ``label`` + ``value`` strings (tab-prop-schemas.ts
``statBannerSchema``, StatBanner.tsx ``StatBannerStat``) — and carries
``meaning`` + ``timestamp`` through the schema's passthrough. Since the phase-1
review (Kfir 2026-10-21, decision 2) nothing is mined from text: a key point's
title holding a figure is no tile.
"""

from __future__ import annotations

from src.services.pipeline.assembly.attachments import (
    _STAT_BANNER_MAX,
    _STAT_BANNER_MIN,
    attach_response_secondaries,
    attach_secondaries,
)

_FIGURES = [
    ("150,000 lines", "in one month"),
    ("3%", "Ruby share of his work"),
    ("95%", "agent eval completion"),
    ("99%", "less backend CPU"),
    ("$20 million", "raised for Omarchy"),
    ("9 seconds", "Omarchy install"),
    ("2 trillion", "photos a year"),
    ("1,000x", "tool gap"),
    ("40,000", "bank tellers after the ATM"),
]


def _key_ideas(n: int, timestamp: bool = True) -> list[dict]:
    return [
        {
            "claim": f"Claim {i} turns on {_FIGURES[i % len(_FIGURES)][0]}",
            "why": "w",
            "kind": "number",
            "figure": {
                "value": _FIGURES[i % len(_FIGURES)][0],
                "label": _FIGURES[i % len(_FIGURES)][1],
            },
            **({"timestamp": 60 * (i + 1)} if timestamp else {}),
        }
        for i in range(n)
    ]


def _extraction(n: int = _STAT_BANNER_MIN, **kwargs) -> dict:
    return {"learning": {"keyIdeas": _key_ideas(n, **kwargs)}}


def _answer_tab() -> dict:
    return {
        "id": "key_arguments",
        "component": "concept_canvas",
        "goal": "Explore the ideas.",
        "props": {"concepts": [{"name": f"c{i}"} for i in range(6)]},
    }


def _banner(attachments: list[dict]) -> dict | None:
    return next((a for a in attachments if a["component"] == "stat_banner"), None)


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
            "meaning": "Claim 0 turns on 150,000 lines",
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

    def test_key_ideas_from_a_secondary_domain_are_used(self):
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

    def test_key_ideas_without_timestamps_do_not_count(self):
        out = attach_secondaries(
            _answer_tab(),
            _extraction(8, timestamp=False),
            None,
            None,
            "learning",
            is_answer_tab=True,
        )

        assert _banner(out) is None

    def test_key_points_with_a_figure_in_the_title_do_not_count(self):
        points = [
            {"title": f"{value} {label}", "detail": "d", "timestamp": 60 * (i + 1)}
            for i, (value, label) in enumerate(_FIGURES)
        ]

        out = attach_secondaries(
            _answer_tab(), {"learning": {"keyPoints": points}}, None, None, "learning",
            is_answer_tab=True,
        )  # fmt: skip

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
