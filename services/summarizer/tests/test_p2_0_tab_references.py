"""No hero line names a tab that does not ship (output-quality 2.0 b, G1-2).

Fixtures are the stored gate-1 docs' own values: S09 / S10 / S11's Moments and
filmstrip frames (data/S0x/doc.json), S09 / S11's masterSummary. No LLM, no I/O.
"""

from __future__ import annotations

import json
from types import SimpleNamespace
from typing import Any

import pytest

from src.services.pipeline.assembly import assemble_response
from src.services.pipeline.assembly.cross_tab import prune_links
from src.services.pipeline.assembly.tab_references import (
    FILMSTRIP_SHADOWED,
    TabNames,
    drop_shadowed_filmstrips,
    frame_backed_count,
    goal_without_lost_structure,
    scrub_sentences,
    scrub_tab_references,
    settle_shipped_tabs,
)
from src.services.pipeline.phases.synthesis import settle_tab_references


def _keyed(video: str, table: str) -> list[tuple[int, str]]:
    """``"160:frames/160 …"`` → ``[(160, "videos/<video>/frames/160.jpg"), …]``."""
    pairs = (entry.split(":", 1) for entry in table.split())
    return [(int(seconds), f"videos/{video}/{path}.jpg") for seconds, path in pairs]


def _frame_url(key: str) -> str:
    return f"https://vie-transcripts.s3.amazonaws.com/{key}"


def _moment_tab(tab_id: str, label: str, keyed: list[tuple[int, str]]) -> dict:
    items = [
        {"label": f"Moment at {s}", "seconds": s, "description": "What it established.",
         "thumbnailUrl": _frame_url(key), "s3Key": key}
        for s, key in keyed
    ]  # fmt: skip
    return {"id": tab_id, "label": label, "component": "moment_track", "props": {"items": items}}


def _filmstrip_tab(tab_id: str, label: str, keyed: list[tuple[int, str]]) -> dict:
    frames = [
        {"thumbnailUrl": _frame_url(key), "timestamp": ts, "caption": f"Scene {ts}", "s3Key": key}
        for ts, key in keyed
    ]
    return {
        "id": tab_id,
        "label": label,
        "component": "video_filmstrip",
        "props": {"frames": frames},
    }


# data/S09/doc.json "highlights" (Pull Moments): 12 framed moments; "frames-gallery": 12 frames.
_S09_MOMENTS = _keyed(
    "uC45_4nnEAI",
    "160:frames/160 200:frames/200 285:scenes-v4/scene_0005 378:frames/378 500:frames/500 "
    "603:frames/603 640:scenes-v4/scene_0011 670:frames/670 715:scenes-v4/scene_0013 "
    "840:frames/840 951:scenes-v4/scene_0017 1062:frames/1062",
)
_S09_FRAMES = _keyed(
    "uC45_4nnEAI",
    " ".join(
        f"{ts}:scenes-v4/scene_{n:04d}"
        for ts, n in [(16, 0), (114, 3), (309, 5), (312, 6), (428, 8), (447, 9), (630, 11),
                      (700, 12), (704, 13), (742, 14), (809, 15), (891, 16)]
    ),
)  # fmt: skip
# data/S11/doc.json "moments": 8 framed moments; "scenes" (Vietnam Scenes, planned): 12 frames.
_S11_MOMENTS = _keyed(
    "vOkkWuEwUmc",
    "0:scenes-v4/scene_0000 480:frames/480 780:frames/780 840:frames/840 1380:frames/1380 "
    "1440:frames/1440 1710:frames/1710 2280:frames/2280",
)
_S11_FRAMES = _keyed("vOkkWuEwUmc", "5:scenes-v4/scene_0000 182:scenes-v4/scene_0063 " * 6)
# data/S10/doc.json "frames-gallery": 10 frames, and no Moments tab.
_S10_FRAMES = _keyed("Jru5B044HOs", "9:scenes-v4/scene_0000 85:scenes-v4/scene_0004 " * 5)

_S09_SUMMARY = (
    "In this exciting video, the Three Lions TCG crew dives into their OP-17 booster box break. "
    "They delve into individual pulls, like the powerful Yamato and the crafty Stussy.\n\n"
    "As the video wraps up, viewers can look forward to detailed tab sections like The Pulls "
    "for a complete list of cards, Pull Moments for timestamps of major highlights, Deck Needs "
    "for insights into which cards are necessary for winning decks, and Visual Moments "
    "showcasing the artwork of the amazing pulls. Whether you're a competitive player or a "
    "collector, this box opening offers a treasure trove of information."
)
_S11_SUMMARY = (
    "Afterward, viewers are treated to the coastal delights of Da Nang, wrapping up with the "
    "enchanting lantern-lit streets of Hoi An. Don’t forget to check out the Day by Day, Moments, "
    "and Vietnam Scenes tabs for a detailed itinerary and highlights of this wonderful journey."
)


def _s09_tabs() -> list[dict]:
    overview = {
        "masterSummary": _S09_SUMMARY,
        "itemCount": 4,
        "stats": [{"label": "Items", "value": "4"}],
    }
    link = {"targetTab": "frames-gallery", "label": "Visual Moments"}
    return [
        {"id": "overview", "label": "Overview", "component": "overview",
         "props": {"data": overview}},
        {"id": "pulls", "label": "The Pulls", "component": "tier_list",
         "props": {"items": [{"name": "Loki"}]}, "crossTabLinks": [link]},
        _moment_tab("highlights", "Pull Moments", _S09_MOMENTS),
        {"id": "deck_needs", "label": "Deck Needs", "component": "spot_explorer",
         "props": {"spots": [{"name": "Yamato"}]}},
        _filmstrip_tab("frames-gallery", "Visual Moments", _S09_FRAMES),
    ]  # fmt: skip


# ─── (b) The filmstrip the framed Moments shadow ───


class TestShadowedFilmstrip:
    def test_should_drop_s09s_filmstrip_when_its_moments_are_framed(self) -> None:
        tabs = _s09_tabs()

        removed = drop_shadowed_filmstrips(tabs)

        assert ([t["id"] for t in removed], "frames-gallery" in [t["id"] for t in tabs]) == (
            ["frames-gallery"],
            False,
        )

    def test_should_drop_s11s_planned_filmstrip_at_exactly_eight_framed_moments(self) -> None:
        tabs = [
            _moment_tab("moments", "Moments", _S11_MOMENTS),
            _filmstrip_tab("scenes", "Vietnam Scenes", _S11_FRAMES),
        ]

        assert [t["id"] for t in drop_shadowed_filmstrips(tabs)] == ["scenes"]

    def test_should_keep_s10s_filmstrip_without_a_moments_tab(self) -> None:
        tabs = [_filmstrip_tab("frames-gallery", "Visual Moments", _S10_FRAMES)]

        assert (drop_shadowed_filmstrips(tabs), len(tabs)) == ([], 1)

    def test_should_keep_the_filmstrip_when_seven_moments_are_framed(self) -> None:
        tabs = [
            _moment_tab("moments", "Moments", _S11_MOMENTS[:7]),
            _filmstrip_tab("scenes", "Vietnam Scenes", _S11_FRAMES),
        ]

        assert drop_shadowed_filmstrips(tabs) == []

    @pytest.mark.parametrize(
        ("item", "framed"),
        [
            ({"thumbnailUrl": "https://s3/f.jpg"}, True),
            ({"s3Key": "videos/x/frames/1.jpg"}, True),  # the API adds the URL
            ({"thumbnailUrl": "", "s3Key": "videos/x/frames/1.jpg"}, True),  # the API refreshes it
            ({"thumbnailUrl": None, "s3Key": "videos/x/frames/1.jpg"}, False),  # a deliberate clear
            ({"s3Key": "transcripts/x.json"}, False),
            ({"thumbnailUrl": ""}, False),
        ],
    )
    def test_should_count_a_frame_as_the_web_sees_it_after_the_url_refresh(
        self, item: dict, framed: bool
    ) -> None:
        assert frame_backed_count({"items": [item]}) == int(framed)

    def test_should_record_a_planned_filmstrip_dropped_at_assembly(self) -> None:
        triage = {
            "primaryTag": "learning",
            "contentTags": ["learning"],
            "tabs": [
                {
                    "id": "moments",
                    "label": "Pull Moments",
                    "component": "moment_track",
                    "dataSource": "learning.keyPoints",
                },
                {
                    "id": "scenes",
                    "label": "🎞️ Vietnam Scenes",
                    "component": "video_filmstrip",
                    "dataSource": "frames",
                },
            ],
        }
        points = [
            {
                "title": f"Point {i}",
                "description": f"The speaker establishes fact {i} with a reason.",
                "timestamp": 120 * i + 5,
            }
            for i in range(10)
        ]
        frames = [
            {
                "timestamp": 120 * i + 5,
                "s3_url": f"https://s3/f{i}.jpg",
                "s3_key": f"videos/abc/frames/{i}.jpg",
            }
            for i in range(12)
        ]
        synthesis = {"masterSummary": "One. See the Pull Moments and Vietnam Scenes tabs. Two."}

        result = assemble_response(
            triage,
            {"learning": {"keyPoints": points}},
            None,
            synthesis,
            {"duration": 1800},
            frames=frames,
        )

        assert (result["dropped"][-1]["reason"], result["meta"]["masterSummary"]) == (
            FILMSTRIP_SHADOWED,
            "One. Two.",
        )


# ─── (b) Nothing names a tab that does not ship ───


def _names(dropped: list[str], shipped: list[str]) -> TabNames:
    return TabNames.of(dropped, shipped)


class TestScrubSentences:
    def test_should_remove_only_s09s_sentence_naming_visual_moments(self) -> None:
        names = _names(["Visual Moments"], ["The Pulls", "Pull Moments", "Deck Needs"])

        scrubbed = scrub_sentences(_S09_SUMMARY, names)

        assert scrubbed == (
            "In this exciting video, the Three Lions TCG crew dives into their OP-17 booster box "
            "break. They delve into individual pulls, like the powerful Yamato and the crafty "
            "Stussy.\n\nWhether you're a competitive player or a collector, this box opening "
            "offers a treasure trove of information."
        )

    def test_should_remove_s11s_sentence_naming_shipped_and_dropped_tabs_together(self) -> None:
        names = _names(["Vietnam Scenes"], ["Day by Day", "Moments"])

        assert scrub_sentences(_S11_SUMMARY, names) == (
            "Afterward, viewers are treated to the coastal delights of Da Nang, wrapping up with "
            "the enchanting lantern-lit streets of Hoi An."
        )

    def test_should_not_read_a_shipped_tabs_longer_label_as_the_dropped_one(self) -> None:
        text = "Pull Moments has the timestamps."

        assert scrub_sentences(text, _names(["Moments"], ["Pull Moments"])) == text

    def test_should_match_whole_words_only(self) -> None:
        text = "The Visual Momentsy tab is fine."

        assert scrub_sentences(text, _names(["Visual Moments"], [])) == text

    def test_should_match_without_case(self) -> None:
        assert (
            scrub_sentences("Keep. See visual moments.", _names(["Visual Moments"], [])) == "Keep."
        )


class TestOneWordLabels:
    """A dropped "Moments" / "Tips" / "Steps" names its tab only in a sentence that reads as
    a tab reference — the word alone is ordinary prose."""

    @pytest.mark.parametrize(
        "sentence",
        ["The key moments come late.", "Moments like these matter.", "He shares tips early."],
    )
    def test_should_keep_a_sentence_that_merely_uses_the_word(self, sentence: str) -> None:
        names = _names(["Moments", "Tips"], [])

        assert scrub_sentences(f"Keep. {sentence}", names) == f"Keep. {sentence}"

    @pytest.mark.parametrize(
        "sentence",
        [
            "Check out the Moments tab for highlights.",
            "The moments section lists them.",
            "Jump to Moments for the reveals.",
        ],
    )
    def test_should_remove_a_sentence_that_reads_as_a_tab_reference(self, sentence: str) -> None:
        assert scrub_sentences(f"Keep. {sentence}", _names(["Moments"], [])) == "Keep."

    def test_should_keep_the_phrase_rule_for_a_multi_word_label(self) -> None:
        text = "Keep. The visual moments are lovely."

        assert scrub_sentences(text, _names(["Visual Moments"], [])) == "Keep."


class TestScrubTabReferences:
    def test_should_scrub_hero_fields_takeaways_and_goals_for_a_planned_tab_not_shipped(
        self,
    ) -> None:
        meta = {
            "tldr": "A trip.",
            "masterSummary": _S11_SUMMARY,
            "seoDescription": "Ten days.",
            "keyTakeaways": ["Book early.", "The Vietnam Scenes tab shows it."],
        }
        tabs = [
            {
                "id": "itinerary",
                "label": "Day by Day",
                "goal": "Follow it. Then open Vietnam Scenes.",
            },
            {"id": "moments", "label": "Moments", "goal": "Jump to each stop."},
        ]
        plan = [{"id": "scenes", "label": "🎞️ Vietnam Scenes"}]

        changed = scrub_tab_references(meta, tabs, plan)

        assert (changed, meta["keyTakeaways"], tabs[0]["goal"]) == (
            3,
            ["Book early."],
            "Follow it.",
        )

    def test_should_leave_a_name_a_shipped_tab_also_carries(self) -> None:
        meta = {"masterSummary": "Open the Moments tab."}
        tabs = [{"id": "key_moments", "label": "🎬 Moments"}]

        assert scrub_tab_references(meta, tabs, [{"id": "moments", "label": "Moments"}]) == 0


class TestSettle:
    def test_should_drop_s09s_filmstrip_and_its_sentence_links_and_count(self) -> None:
        tabs = _s09_tabs()
        meta = {"masterSummary": _S09_SUMMARY}

        settle_shipped_tabs(meta, tabs, [*tabs], {})

        overview = tabs[0]["props"]["data"]
        assert (
            "Visual Moments" in meta["masterSummary"] + overview["masterSummary"],
            tabs[1]["crossTabLinks"],
            overview["itemCount"],
            overview["stats"][0]["value"],
        ) == (False, [], 3, "3")

    def test_should_send_a_corrected_hero_after_the_late_group(self) -> None:
        tabs = _s09_tabs()
        hero = {
            "tldr": "T",
            "masterSummary": _S09_SUMMARY,
            "keyTakeaways": [],
            "seoDescription": "",
        }
        ctx = SimpleNamespace(
            assembled_meta=dict(hero),
            assembled_tabs=tabs,
            triage_dict={"tabs": []},
            synthesis_dict=dict(hero),
        )
        assembled: dict[str, Any] = {"tabs": tabs, "dropped": []}

        event = settle_tab_references(ctx, assembled)  # type: ignore[arg-type]

        assert event is not None
        payload = json.loads(event.removeprefix("data: "))
        assert (payload["event"], "Visual Moments" in payload["masterSummary"]) == (
            "synthesis_complete",
            False,
        )

    def test_should_record_the_planned_filmstrip_and_send_nothing_when_the_hero_is_clean(
        self,
    ) -> None:
        tabs = [
            _moment_tab("moments", "Moments", _S11_MOMENTS),
            _filmstrip_tab("scenes", "Vietnam Scenes", _S11_FRAMES),
        ]
        hero = {"tldr": "T", "masterSummary": "A trip.", "keyTakeaways": [], "seoDescription": ""}
        plan = [{"id": "scenes", "label": "🎞️ Vietnam Scenes", "dataSource": "frames"}]
        ctx = SimpleNamespace(
            assembled_meta=dict(hero),
            assembled_tabs=tabs,
            triage_dict={"tabs": plan},
            synthesis_dict=dict(hero),
        )
        assembled: dict[str, Any] = {"tabs": tabs, "dropped": []}

        event = settle_tab_references(ctx, assembled)  # type: ignore[arg-type]

        assert (event, [d["id"] for d in assembled["dropped"]]) == (None, ["scenes"])


class TestPruneLinks:
    def test_should_remove_a_link_to_a_tab_that_does_not_ship(self) -> None:
        tabs = [
            {"id": "a", "crossTabLinks": [{"targetTab": "gone"}, {"targetTab": "b"}]},
            {"id": "b"},
        ]

        assert (prune_links(tabs), tabs[0]["crossTabLinks"]) == (1, [{"targetTab": "b"}])


class TestDegradedGoal:
    def test_should_drop_the_tier_clause_when_a_tier_list_shipped_untiered(self) -> None:
        goal = "After this you know which pulls mattered and why each sits in its tier."

        assert goal_without_lost_structure(goal, "tier_list") == (
            "After this you know which pulls mattered."
        )

    def test_should_keep_the_goal_when_the_tab_was_not_degraded(self) -> None:
        goal = "After this you know which pulls mattered and why each sits in its tier."

        assert goal_without_lost_structure(goal, None) == goal

    def test_should_keep_the_goal_when_its_opening_clause_names_the_tiers(self) -> None:
        goal = "After this you know each card's tier and why."

        assert goal_without_lost_structure(goal, "tier_list") == goal
