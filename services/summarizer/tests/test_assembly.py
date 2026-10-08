"""Assembly orchestration tests — data resolution, cross-tab links, assemble_response, frame utilities."""

from src.services.pipeline.assembly import (
    assemble_response,
    find_nearest_frame,
    inject_frame_thumbnails,
    resolve_cross_tab_links,
    resolve_data_source,
)

# ─── resolve_data_source ───


class TestResolveDataSource:
    def test_simple_path(self):
        extraction = {"travel": {"itinerary": [{"day": 1}]}}
        assert resolve_data_source("travel.itinerary", extraction) == [{"day": 1}]

    def test_single_key(self):
        extraction = {"fitness": {"exercises": []}}
        assert resolve_data_source("fitness", extraction) == {"exercises": []}

    def test_enrichment_path(self):
        enrichment = {"quiz": [{"q": "test"}]}
        assert resolve_data_source("enrichment.quiz", {}, enrichment) == [{"q": "test"}]

    def test_missing_path(self):
        assert resolve_data_source("travel.budget", {"travel": {}}) is None

    def test_empty_source(self):
        assert resolve_data_source("", {}) is None

    def test_enrichment_none(self):
        assert resolve_data_source("enrichment.quiz", {}, None) is None

    def test_wildcard_returns_domain_object(self):
        extraction = {"food": {"ingredients": [{"name": "salt"}], "tips": []}}
        result = resolve_data_source("food.*", extraction)
        assert result == extraction["food"]

    def test_wildcard_enrichment(self):
        enrichment = {"quiz": [{"q": "test"}], "flashcards": []}
        result = resolve_data_source("enrichment.*", {}, enrichment)
        assert result == enrichment

    def test_wildcard_missing_domain(self):
        assert resolve_data_source("music.*", {"food": {}}) is None


# ─── Cross-Tab Links ───


class TestCrossTabLinks:
    def test_resolves_legacy_links(self):
        """Legacy ID-based rules still work as fallback."""
        links = resolve_cross_tab_links("concepts", {"concepts", "quizzes", "flashcards"})
        assert len(links) == 1
        assert links[0]["targetTab"] == "quizzes"

    def test_component_based_links(self):
        """Component-based rules with domain hint."""
        all_tabs = [
            {"id": "overview", "component": "overview"},
            {"id": "ingredients", "component": "checklist"},
            {"id": "recipe_steps", "component": "step_player"},
        ]
        links = resolve_cross_tab_links(
            "overview",
            {"overview", "ingredients", "recipe_steps"},
            component="overview",
            all_tabs=all_tabs,
            primary_tag="food",
        )
        assert len(links) >= 1
        targets = {l["targetTab"] for l in links}
        assert "ingredients" in targets

    def test_drops_missing_targets(self):
        links = resolve_cross_tab_links("concepts", {"concepts"})
        assert links == []

    def test_no_rules_for_tab(self):
        links = resolve_cross_tab_links("nonexistent", {"a", "b"})
        assert links == []

    def test_component_link_travel_flow(self):
        """Travel domain: overview → spot_explorer → budget. Label text is
        the target tab's own label."""
        all_tabs = [
            {"id": "overview", "component": "overview", "label": "Overview"},
            {"id": "itinerary", "component": "spot_explorer", "label": "7 Day Itinerary"},
            {"id": "budget", "component": "budget", "label": "Budget"},
        ]
        links = resolve_cross_tab_links(
            "overview",
            {"overview", "itinerary", "budget"},
            component="overview",
            all_tabs=all_tabs,
            primary_tag="travel",
        )
        assert len(links) >= 1
        assert links[0]["targetTab"] == "itinerary"
        assert links[0]["label"] == "7 Day Itinerary"


# ─── Cross-Tab Link Updates ───


class TestMaterialsStepsCrossLinks:
    def test_steps_to_materials(self):
        links = resolve_cross_tab_links("steps", {"steps", "materials"})
        assert any(l["targetTab"] == "materials" for l in links)

    def test_materials_to_steps(self):
        # Legacy ID rule fires structurally; without all_tabs the label is
        # the target tab id.
        links = resolve_cross_tab_links("materials", {"materials", "steps"})
        assert any(l["targetTab"] == "steps" for l in links)


class TestNewCrossTabLinks:
    """Tests for component-based cross-tab link rules."""

    def test_code_setup_bidirectional(self):
        links_code = resolve_cross_tab_links("code", {"code", "setup"})
        links_setup = resolve_cross_tab_links("setup", {"code", "setup"})
        assert any(l["targetTab"] == "setup" for l in links_code)
        assert any(l["targetTab"] == "code" for l in links_setup)

    def test_link_only_if_target_exists(self):
        links = resolve_cross_tab_links("key_points", {"key_points"})
        assert not any(l["targetTab"] == "flashcards" for l in links)

    def test_food_domain_flow(self):
        """Food domain: overview → checklist → step_player."""
        all_tabs = [
            {"id": "overview", "component": "overview"},
            {"id": "ingredients", "component": "checklist"},
            {"id": "steps", "component": "step_player"},
        ]
        ids = {"overview", "ingredients", "steps"}
        links = resolve_cross_tab_links("overview", ids, "overview", all_tabs, "food")
        assert any(
            l["targetTab"] == "ingredients" and "ingredient" in l["label"].lower() for l in links
        )

    def test_learning_domain_flow(self):
        """Learning domain: overview → flash_deck → quiz."""
        all_tabs = [
            {"id": "overview", "component": "overview"},
            {"id": "concepts", "component": "flash_deck"},
            {"id": "quiz", "component": "quiz"},
        ]
        ids = {"overview", "concepts", "quiz"}
        links = resolve_cross_tab_links("overview", ids, "overview", all_tabs, "learning")
        assert any(l["targetTab"] == "concepts" for l in links)

    def test_generic_fallback_rules(self):
        """Generic rules fire when no domain-specific rule matches."""
        all_tabs = [
            {"id": "overview", "component": "overview"},
            {"id": "exercises", "component": "workout_room"},
        ]
        ids = {"overview", "exercises"}
        links = resolve_cross_tab_links("overview", ids, "overview", all_tabs, "narrative")
        assert any(l["targetTab"] == "exercises" for l in links)


# ─── Full Assembly ───


class TestAssembleResponse:
    def _make_triage(self, **overrides):
        base = {
            "contentTags": ["learning"],
            "modifiers": [],
            "primaryTag": "learning",
            "userGoal": "Learn something",
            "tabs": [
                {
                    "id": "key_points",
                    "label": "Key Points",
                    "emoji": "💡",
                    "dataSource": "learning.keyPoints",
                },
                {
                    "id": "concepts",
                    "label": "Concepts",
                    "emoji": "📚",
                    "dataSource": "learning.concepts",
                },
            ],
        }
        base.update(overrides)
        return base

    def test_basic_assembly(self):
        triage = self._make_triage()
        extraction = {
            "learning": {
                "keyPoints": [{"title": "Point 1", "detail": "Detail 1"}],
                # concept_canvas (post-overhaul) requires ≥2 concepts; provide
                # two so the canvas assembler can produce a tab and the test
                # exercises the new routing path.
                "concepts": [
                    {"name": "Concept 1", "definition": "Def 1", "connections": ["Concept 2"]},
                    {"name": "Concept 2", "definition": "Def 2", "connections": ["Concept 1"]},
                ],
            },
        }
        result = assemble_response(triage, extraction, None, None)

        assert "meta" in result
        assert "tabs" in result
        assert result["meta"]["primaryTag"] == "learning"
        assert len(result["tabs"]) == 2
        assert result["tabs"][0]["component"] == "display_section"
        # "concepts" tab routes to concept_canvas post-overhaul (was flash_deck).
        assert result["tabs"][1]["component"] == "concept_canvas"

    def test_drops_empty_tabs(self):
        triage = self._make_triage()
        extraction = {"learning": {"keyPoints": [{"title": "P1"}], "concepts": []}}
        result = assemble_response(triage, extraction, None, None)

        tab_ids = [t["id"] for t in result["tabs"]]
        assert "key_points" in tab_ids
        assert "concepts" not in tab_ids  # Dropped — empty list

    def test_travel_full(self):
        triage = {
            "contentTags": ["travel"],
            "modifiers": ["finance"],
            "primaryTag": "travel",
            "userGoal": "Plan a trip",
            "tabs": [
                {
                    "id": "itinerary",
                    "label": "Itinerary",
                    "emoji": "📅",
                    "dataSource": "travel.itinerary",
                },
                {"id": "budget", "label": "Budget", "emoji": "💰", "dataSource": "travel.budget"},
                {
                    "id": "packing",
                    "label": "Packing",
                    "emoji": "🎒",
                    "dataSource": "travel.packingList",
                },
            ],
        }
        extraction = {
            "travel": {
                "itinerary": [
                    {
                        "day": 1,
                        "city": "Rome",
                        "spots": [
                            {"name": "Colosseum", "description": "Ancient amphitheatre"},
                            {"name": "Forum", "description": "Political heart of old Rome"},
                        ],
                    },
                ],
                "budget": {
                    "total": 2000,
                    "currency": "USD",
                    "breakdown": [{"item": "Hotels", "amount": 1200}],
                },
                # packing_mission needs ≥2 items post-overhaul
                "packingList": [
                    {"item": "Sunscreen", "category": "Essentials", "essential": True},
                    {"item": "Charger", "category": "Tech", "essential": True},
                ],
            },
        }
        result = assemble_response(triage, extraction, None, None)

        # Overview is auto-prepended when domain data has primitives (budget total/currency here).
        assert len(result["tabs"]) == 4
        assert result["tabs"][0]["component"] == "overview"
        assert result["tabs"][1]["component"] == "spot_explorer"
        assert len(result["tabs"][1]["props"]["spots"]) == 2
        assert result["tabs"][2]["component"] == "budget"
        assert result["tabs"][2]["props"]["total"] == 2000
        assert result["tabs"][2]["props"]["currency"] == "USD"
        # Travel packing tab routes to packing_mission post-overhaul.
        assert result["tabs"][3]["component"] == "packing_mission"

    def test_cross_tab_links_resolved(self):
        triage = self._make_triage(
            tabs=[
                {
                    "id": "concepts",
                    "label": "Concepts",
                    "emoji": "📚",
                    "dataSource": "learning.concepts",
                },
                {"id": "quizzes", "label": "Quiz", "emoji": "❓", "dataSource": "enrichment.quiz"},
            ]
        )
        # concept_canvas requires ≥2 concepts post-overhaul
        extraction = {
            "learning": {
                "concepts": [
                    {"name": "A", "definition": "B", "connections": ["C"]},
                    {"name": "C", "definition": "D", "connections": ["A"]},
                ]
            }
        }
        enrichment = {
            "quiz": [
                {"question": "Q1", "options": ["a", "b"], "correctIndex": 0, "explanation": "E"}
            ]
        }
        result = assemble_response(triage, extraction, enrichment, None)

        concepts_tab = next(t for t in result["tabs"] if t["id"] == "concepts")
        assert any(l["targetTab"] == "quizzes" for l in concepts_tab["crossTabLinks"])

    def test_explicit_component_field(self):
        triage = {
            "contentTags": ["learning"],
            "modifiers": [],
            "primaryTag": "learning",
            "userGoal": "Learn",
            "tabs": [
                {
                    "id": "key_points",
                    "label": "Points",
                    "emoji": "💡",
                    "dataSource": "learning.keyPoints",
                    "component": "moment_track",
                },
            ],
        }
        extraction = {"learning": {"keyPoints": [{"title": "P1"}]}}
        result = assemble_response(triage, extraction, None, None)

        assert result["tabs"][0]["component"] == "moment_track"

    def test_synthesis_merged_into_meta(self):
        triage = self._make_triage()
        extraction = {
            "learning": {
                "keyPoints": [{"title": "P1"}],
                "concepts": [{"name": "C", "definition": "D"}],
            }
        }
        synthesis = {
            "tldr": "Summary",
            "keyTakeaways": ["T1"],
            "masterSummary": "Full",
            "seoDescription": "Desc",
        }
        result = assemble_response(triage, extraction, None, synthesis)

        assert result["meta"]["tldr"] == "Summary"
        assert result["meta"]["seoDescription"] == "Desc"
        assert result["meta"]["masterSummary"] == "Full"
        assert result["meta"]["keyTakeaways"] == ["T1"]
        assert "synthesis" not in result

    def test_fitness_exercises_fallback(self):
        triage = {
            "contentTags": ["fitness"],
            "modifiers": [],
            "primaryTag": "fitness",
            "userGoal": "Workout",
            "tabs": [
                {
                    "id": "exercises",
                    "label": "Exercises",
                    "emoji": "💪",
                    "dataSource": "fitness.exercises",
                },
            ],
        }
        extraction = {
            "fitness": {
                "exercises": [{"name": "Push-up"}],
                "warmup": [{"name": "Stretching"}],
                "cooldown": [],
            },
        }
        result = assemble_response(triage, extraction, None, None)

        assert len(result["tabs"]) == 1
        # Video-to-action overhaul: "exercises" tab now routes to workout_room
        # (was exercise_tracker). The assembler shares the same data contract.
        assert result["tabs"][0]["component"] == "workout_room"
        assert len(result["tabs"][0]["props"]["exercises"]) == 1

    def test_description_analysis_in_meta(self):
        triage = self._make_triage()
        extraction = {"learning": {"keyPoints": [{"title": "P1"}]}}
        desc = {"links": [{"url": "https://example.com", "type": "resource", "label": "Test"}]}
        result = assemble_response(triage, extraction, None, None, description_analysis=desc)

        assert result["meta"]["descriptionAnalysis"] == desc
        assert result["meta"]["primaryTag"] == "learning"


# ─── Frame Utilities ───


class TestFrameUtilities:
    def test_find_nearest_frame_exact_match(self):
        frames = [
            {"timestamp": 10.0, "s3_url": "url1"},
            {"timestamp": 30.0, "s3_url": "url2"},
            {"timestamp": 60.0, "s3_url": "url3"},
        ]
        result = find_nearest_frame(30.0, frames)
        assert result is not None
        assert result["s3_url"] == "url2"

    def test_find_nearest_frame_within_distance(self):
        frames = [{"timestamp": 10.0, "s3_url": "url1"}]
        result = find_nearest_frame(25.0, frames, max_distance=30.0)
        assert result is not None

    def test_find_nearest_frame_too_far(self):
        frames = [{"timestamp": 10.0, "s3_url": "url1"}]
        result = find_nearest_frame(100.0, frames, max_distance=30.0)
        assert result is None

    def test_find_nearest_frame_empty(self):
        assert find_nearest_frame(10.0, []) is None

    def test_inject_frame_thumbnails(self):
        items = [
            {"label": "Intro", "seconds": 5},
            {"label": "Main", "seconds": 35},
            {"label": "No timestamp"},
        ]
        frames = [
            {"timestamp": 10.0, "s3_url": "url1"},
            {"timestamp": 30.0, "s3_url": "url2"},
        ]
        inject_frame_thumbnails(items, frames)
        assert items[0].get("thumbnailUrl") == "url1"
        assert items[1].get("thumbnailUrl") == "url2"
        assert "thumbnailUrl" not in items[2]

    def test_inject_frame_thumbnails_no_frames(self):
        items = [{"label": "Test", "seconds": 5}]
        inject_frame_thumbnails(items, [])
        assert "thumbnailUrl" not in items[0]

    def test_inject_frame_thumbnails_preserves_s3_key(self):
        """API re-signs URLs at read time from s3Key, so the injector MUST
        carry s3_key through alongside the at-generation-time thumbnailUrl."""
        items = [{"label": "Intro", "seconds": 5}]
        frames = [
            {"timestamp": 10.0, "s3_url": "fresh-url", "s3_key": "videos/abc/frames/10.jpg"},
        ]
        inject_frame_thumbnails(items, frames)
        assert items[0]["thumbnailUrl"] == "fresh-url"
        assert items[0]["s3Key"] == "videos/abc/frames/10.jpg"

    def test_inject_frame_thumbnails_no_s3_key_no_field(self):
        """Frames without s3_key (e.g. dev fixtures) should not add an s3Key field."""
        items = [{"label": "Intro", "seconds": 5}]
        frames = [{"timestamp": 10.0, "s3_url": "url1"}]
        inject_frame_thumbnails(items, frames)
        assert items[0]["thumbnailUrl"] == "url1"
        assert "s3Key" not in items[0]


class TestFrameMetadataInjection:
    """Tests for the assembler's frame-metadata wiring (Phase 2)."""

    def test_attaches_vision_caption_to_item(self):
        from src.services.pipeline.assembly.core import inject_frame_thumbnails

        frames = [
            {"timestamp": 60.0, "s3_url": "https://s3/frame60.jpg", "s3_key": "frame60"},
        ]
        descriptions = [
            {
                "timestamp_sec": 60.0,
                "scene_type": "code",
                "content": "Python function implementing binary search",
                "text_visible": "def binary_search(arr, target):",
                "educational_value": "Shows the exact implementation discussed",
            }
        ]
        items = [{"timestamp": 60.0, "label": "Binary search demo"}]
        inject_frame_thumbnails(items, frames, frame_descriptions=descriptions)

        assert items[0]["thumbnailUrl"] == "https://s3/frame60.jpg"
        assert items[0]["frameCaption"] == "Python function implementing binary search"
        assert items[0]["frameSceneType"] == "code"
        assert items[0]["frameOcr"] == "def binary_search(arr, target):"
        assert items[0]["frameEvidence"].startswith("Shows the exact")

    def test_skips_talking_head_without_educational_value(self):
        from src.services.pipeline.assembly.core import inject_frame_thumbnails

        frames = [{"timestamp": 30.0, "s3_url": "https://s3/f30.jpg"}]
        descriptions = [
            {
                "timestamp_sec": 30.0,
                "scene_type": "talking_head",
                "content": "Presenter looking at camera",
                "educational_value": "",
            }
        ]
        items = [{"timestamp": 30.0, "label": "Intro"}]
        inject_frame_thumbnails(items, frames, frame_descriptions=descriptions)

        # Filler frames are declined entirely — attaching a presenter crop as
        # "evidence" pretends the frame supports the claim when it doesn't.
        assert "thumbnailUrl" not in items[0]
        assert "frameCaption" not in items[0]

    def test_keeps_talking_head_when_marked_educational(self):
        from src.services.pipeline.assembly.core import inject_frame_thumbnails

        frames = [{"timestamp": 30.0, "s3_url": "https://s3/f30.jpg"}]
        descriptions = [
            {
                "timestamp_sec": 30.0,
                "scene_type": "talking_head",
                "content": "Presenter holding the product up",
                "educational_value": "Shows scale of the device next to a hand",
            }
        ]
        items = [{"timestamp": 30.0, "label": "Size demo"}]
        inject_frame_thumbnails(items, frames, frame_descriptions=descriptions)

        assert items[0]["frameCaption"] == "Presenter holding the product up"
        assert "frameEvidence" in items[0]


class TestFrameExclusivity:
    """A frame serves one moment — reuse only for ~equal timestamps across tabs."""

    @staticmethod
    def _frames():
        return [
            {"timestamp": 100.0, "s3_url": "https://s3/f100.jpg", "s3_key": "f100"},
            {"timestamp": 300.0, "s3_url": "https://s3/f300.jpg", "s3_key": "f300"},
        ]

    def test_different_timestamps_never_share_a_frame(self):
        # Both items are nearest to f100 but describe different moments —
        # the second gets NO thumbnail rather than a wrong duplicate.
        used: dict[str, float] = {}
        tab_a: set[str] = set()
        tab_b: set[str] = set()
        items_a = [{"timestamp": 95.0, "label": "moment A"}]
        items_b = [{"timestamp": 110.0, "label": "moment B"}]

        inject_frame_thumbnails(items_a, self._frames(), used=used, tab_used=tab_a)
        inject_frame_thumbnails(items_b, self._frames(), used=used, tab_used=tab_b)

        assert items_a[0]["thumbnailUrl"] == "https://s3/f100.jpg"
        assert "thumbnailUrl" not in items_b[0]

    def test_equal_timestamps_share_across_tabs(self):
        used: dict[str, float] = {}
        items_a = [{"timestamp": 100.0, "label": "spot"}]
        items_b = [{"timestamp": 100.0, "label": "moment"}]

        inject_frame_thumbnails(items_a, self._frames(), used=used, tab_used=set())
        inject_frame_thumbnails(items_b, self._frames(), used=used, tab_used=set())

        assert items_a[0]["thumbnailUrl"] == "https://s3/f100.jpg"
        assert items_b[0]["thumbnailUrl"] == "https://s3/f100.jpg"

    def test_equal_timestamps_never_share_within_a_tab(self):
        used: dict[str, float] = {}
        tab: set[str] = set()
        items = [
            {"timestamp": 100.0, "label": "row 1"},
            {"timestamp": 100.0, "label": "row 2"},
        ]

        inject_frame_thumbnails(items, self._frames(), used=used, tab_used=tab)

        assert items[0]["thumbnailUrl"] == "https://s3/f100.jpg"
        assert "thumbnailUrl" not in items[1]

    def test_second_item_falls_through_to_next_eligible_frame(self):
        # When the nearest frame is taken, a farther-but-in-range frame wins
        # over attaching nothing.
        frames = [
            {"timestamp": 100.0, "s3_url": "https://s3/f100.jpg", "s3_key": "f100"},
            {"timestamp": 120.0, "s3_url": "https://s3/f120.jpg", "s3_key": "f120"},
        ]
        used: dict[str, float] = {}
        tab: set[str] = set()
        items = [
            {"timestamp": 100.0, "label": "first"},
            {"timestamp": 105.0, "label": "second"},
        ]

        inject_frame_thumbnails(items, frames, used=used, tab_used=tab)

        assert items[0]["thumbnailUrl"] == "https://s3/f100.jpg"
        assert items[1]["thumbnailUrl"] == "https://s3/f120.jpg"

    def test_declined_filler_frame_stays_assignable(self):
        # A frame declined as filler for one item is NOT marked used and can
        # still serve an item whose description allows it.
        frames = [{"timestamp": 30.0, "s3_url": "https://s3/f30.jpg", "s3_key": "f30"}]
        descriptions = [
            {
                "timestamp_sec": 30.0,
                "scene_type": "talking_head",
                "content": "Presenter",
                "educational_value": "",
            }
        ]
        used: dict[str, float] = {}
        items_a = [{"timestamp": 30.0, "label": "filler target"}]
        items_b = [{"timestamp": 30.0, "label": "other item"}]

        inject_frame_thumbnails(
            items_a, frames, frame_descriptions=descriptions, used=used, tab_used=set()
        )
        assert "thumbnailUrl" not in items_a[0]
        assert used == {}

        inject_frame_thumbnails(items_b, frames, used=used, tab_used=set())
        assert items_b[0]["thumbnailUrl"] == "https://s3/f30.jpg"


class TestDropAccounting:
    """assemble_response returns honest per-tab drop records in `dropped`."""

    @staticmethod
    def _triage(tabs: list[dict], primary: str = "learning") -> dict:
        return {
            "contentTags": [primary],
            "modifiers": [],
            "primaryTag": primary,
            "userGoal": "goal",
            "tabs": tabs,
        }

    def test_clean_run_returns_empty_dropped(self):
        triage = self._triage(
            [
                {
                    "id": "key_points",
                    "label": "Key Points",
                    "emoji": "💡",
                    "dataSource": "learning.keyPoints",
                }
            ]
        )
        extraction = {"learning": {"keyPoints": [{"title": "P1", "detail": "D1"}]}}
        result = assemble_response(triage, extraction, None, None)

        assert result["dropped"] == []

    def test_empty_datasource_drop_is_recorded_with_reason(self):
        triage = self._triage(
            [
                {
                    "id": "key_points",
                    "label": "Key Points",
                    "emoji": "💡",
                    "dataSource": "learning.keyPoints",
                },
                {
                    "id": "concepts",
                    "label": "Concepts",
                    "emoji": "📚",
                    "dataSource": "learning.concepts",
                },
            ]
        )
        extraction = {"learning": {"keyPoints": [{"title": "P1"}], "concepts": []}}
        result = assemble_response(triage, extraction, None, None)

        assert "concepts" not in [t["id"] for t in result["tabs"]]
        dropped_ids = [d["id"] for d in result["dropped"]]
        assert dropped_ids == ["concepts"]
        entry = result["dropped"][0]
        assert entry["reason"] in {"assembler_returned_none", "validation_empty_list"}
        assert entry["dataSource"] == "learning.concepts"


class TestGenericInfoGridFallback:
    """An info_grid tab whose planned field is empty falls back to the longest
    populated list in the same domain instead of dying (Vietnam `hotels`,
    unboxing `set_info`/review.specs)."""

    def test_info_grid_recovers_from_unregistered_empty_field(self):
        triage = {
            "contentTags": ["travel"],
            "modifiers": [],
            "primaryTag": "travel",
            "userGoal": "Plan a trip",
            "tabs": [
                {
                    "id": "hotels",
                    "label": "Recommended Stays",
                    "emoji": "🏨",
                    "component": "info_grid",
                    "dataSource": "travel.hotels",
                },
            ],
        }
        extraction = {
            "travel": {
                "hotels": [],
                "localCustoms": [
                    {"key": "Greeting", "value": "Bow slightly"},
                    {"key": "Tipping", "value": "Not expected"},
                    {"key": "Dress", "value": "Cover shoulders at temples"},
                ],
            }
        }
        result = assemble_response(triage, extraction, None, None)

        hotels = [t for t in result["tabs"] if t["id"] == "hotels"]
        assert hotels, f"hotels tab was dropped: {result['dropped']}"
        assert hotels[0]["component"] == "info_grid"
        assert len(hotels[0]["props"]["items"]) == 3


class TestInjectorRetryOnRefusal:
    """A filler (talking_head) refusal must not cost the item its thumbnail
    when the next-nearest in-range frame is real content."""

    def test_retries_past_talking_head_to_content_frame(self):
        frames = [
            {"timestamp": 100.0, "s3_url": "https://s3/talk.jpg", "s3_key": "talk"},
            {"timestamp": 110.0, "s3_url": "https://s3/card.jpg", "s3_key": "card"},
        ]
        descriptions = [
            {
                "timestamp_sec": 100.0,
                "scene_type": "talking_head",
                "content": "Presenter",
                "educational_value": "",
            },
            {
                "timestamp_sec": 110.0,
                "scene_type": "product_demo",
                "content": "Card held up",
                "educational_value": "shows the card",
            },
        ]
        items = [{"timestamp": 101.0, "label": "big pull"}]

        inject_frame_thumbnails(
            items, frames, frame_descriptions=descriptions, used={}, tab_used=set()
        )

        assert items[0]["thumbnailUrl"] == "https://s3/card.jpg"

    def test_all_candidates_refused_leaves_item_bare(self):
        frames = [
            {"timestamp": 100.0, "s3_url": "https://s3/t1.jpg", "s3_key": "t1"},
            {"timestamp": 108.0, "s3_url": "https://s3/t2.jpg", "s3_key": "t2"},
        ]
        descriptions = [
            {"timestamp_sec": 100.0, "scene_type": "talking_head", "educational_value": ""},
            {"timestamp_sec": 108.0, "scene_type": "talking_head", "educational_value": ""},
        ]
        items = [{"timestamp": 102.0, "label": "moment"}]

        inject_frame_thumbnails(
            items, frames, frame_descriptions=descriptions, used={}, tab_used=set()
        )

        assert "thumbnailUrl" not in items[0]

    def test_refused_frame_not_marked_used(self):
        frames = [{"timestamp": 100.0, "s3_url": "https://s3/t1.jpg", "s3_key": "t1"}]
        descriptions = [
            {"timestamp_sec": 100.0, "scene_type": "talking_head", "educational_value": ""},
        ]
        items = [{"timestamp": 100.0, "label": "moment"}]
        used: dict[str, float] = {}

        inject_frame_thumbnails(
            items, frames, frame_descriptions=descriptions, used=used, tab_used=set()
        )

        assert used == {}


class TestDegradeNeverDrop:
    """assemble_response degrades failing tabs instead of dropping them."""

    def test_string_step_tab_survives(self):
        triage = {
            "contentTags": ["tech"],
            "primaryTag": "tech",
            "userGoal": "g",
            "tabs": [
                {
                    "id": "workflow_stages",
                    "label": "Workflow",
                    "emoji": "🛠",
                    "component": "step_flow_canvas",
                    "dataSource": "tech.setup",
                },
            ],
        }
        extraction = {"tech": {"setup": ["git clone repo", "npm install", "npm run dev"]}}
        result = assemble_response(triage, extraction, None, None)

        tab_ids = [t["id"] for t in result["tabs"]]
        assert "workflow_stages" in tab_ids
        assert not any(d["id"] == "workflow_stages" for d in result["dropped"])

    def test_degraded_tab_carries_degraded_from(self):
        triage = {
            "contentTags": ["gaming"],
            "primaryTag": "gaming",
            "userGoal": "g",
            "tabs": [
                {
                    "id": "rankings",
                    "label": "Rankings",
                    "emoji": "⭐",
                    "component": "tier_list",
                    "dataSource": "gaming.rankings",
                },
            ],
        }
        # Only 2 items — below tier_list min 3 → demote to info_grid.
        extraction = {
            "gaming": {
                "rankings": [
                    {"item": "Card A", "reason": "chase"},
                    {"item": "Card B", "reason": "solid"},
                ]
            }
        }
        result = assemble_response(triage, extraction, None, None)

        tab = next((t for t in result["tabs"] if t["id"] == "rankings"), None)
        assert tab is not None
        assert tab["degradedFrom"] == "tier_list"
        assert tab["component"] == "info_grid"

    def test_truly_empty_data_still_drops(self):
        triage = {
            "contentTags": ["gaming"],
            "primaryTag": "gaming",
            "userGoal": "g",
            "tabs": [
                {
                    "id": "rankings",
                    "label": "Rankings",
                    "emoji": "⭐",
                    "component": "tier_list",
                    "dataSource": "gaming.rankings",
                },
            ],
        }
        result = assemble_response(triage, {"gaming": {"rankings": []}}, None, None)

        assert not any(t["id"] == "rankings" for t in result["tabs"])


class TestNoDoubleFilmstrip:
    def test_auto_gallery_skipped_when_planned_filmstrip_exists(self):
        frames = [
            {
                "timestamp": float(i * 30),
                "s3_url": f"https://s3/f{i}.jpg",
                "s3_key": f"f{i}",
                "ocr_text": f"caption {i}",
            }
            for i in range(12)
        ]
        descriptions = [
            {"timestamp_sec": float(i * 30), "scene_type": "location", "content": f"Scene {i}"}
            for i in range(12)
        ]
        triage = {
            "contentTags": ["travel"],
            "primaryTag": "travel",
            "userGoal": "g",
            "tabs": [
                {
                    "id": "scenes",
                    "label": "Scenes",
                    "emoji": "🎞",
                    "component": "video_filmstrip",
                    "dataSource": "frames",
                },
                {
                    "id": "itinerary",
                    "label": "Route",
                    "emoji": "🧭",
                    "component": "spot_explorer",
                    "dataSource": "travel.itinerary",
                },
            ],
        }
        extraction = {
            "travel": {
                "itinerary": [
                    {"name": f"Spot {i}", "description": "d" * 30, "timestamp": i * 30}
                    for i in range(4)
                ]
            }
        }
        result = assemble_response(
            triage,
            extraction,
            None,
            None,
            frames=frames,
            gallery_frames=frames,
            frame_descriptions=descriptions,
        )

        filmstrips = [t for t in result["tabs"] if t["component"] == "video_filmstrip"]
        assert len(filmstrips) == 1
        assert filmstrips[0]["id"] == "scenes"


class TestMomentThumbnailBackfill:
    """Relaxed second pass fills frameless moment items for the grid view."""

    @staticmethod
    def _triage():
        return {
            "contentTags": ["gaming"],
            "primaryTag": "gaming",
            "userGoal": "g",
            "tabs": [
                {
                    "id": "pulls",
                    "label": "Pulls",
                    "emoji": "⭐",
                    "component": "tier_list",
                    "dataSource": "gaming.rankings",
                },
                {
                    "id": "highlights",
                    "label": "Highlights",
                    "emoji": "🎬",
                    "component": "moment_track",
                    "dataSource": "gaming.highlights",
                },
            ],
        }

    def test_helper_fills_frameless_items_ignoring_cross_tab_ledger(self):
        from src.services.pipeline.assembly.core import _backfill_moment_thumbnails

        tabs = [
            {
                "component": "moment_track",
                "props": {"items": [{"label": "m", "seconds": 108}]},
            }
        ]
        frames = [{"timestamp": 100.0, "s3_url": "https://s3/f100.jpg", "s3_key": "f100"}]
        # The response-wide ledger claimed f100 elsewhere — irrelevant here:
        # the relaxed pass deliberately ignores cross-tab usage.
        _backfill_moment_thumbnails(tabs, frames, None, None)

        assert tabs[0]["props"]["items"][0]["thumbnailUrl"] == "https://s3/f100.jpg"

    def test_backfill_never_duplicates_within_the_tab(self):
        frames = [{"timestamp": 100.0, "s3_url": "https://s3/f100.jpg", "s3_key": "f100"}]
        extraction = {
            "gaming": {
                "rankings": [
                    {"item": "A", "tier": "S", "reason": "r", "timestamp": 500},
                    {"item": "B", "tier": "A", "reason": "r", "timestamp": 600},
                    {"item": "C", "tier": "B", "reason": "r", "timestamp": 700},
                ],
                "highlights": [
                    {"title": "First", "timestamp": 100, "description": "d"},
                    {"title": "Second", "timestamp": 112, "description": "d"},
                ],
            }
        }
        result = assemble_response(self._triage(), extraction, None, None, frames=frames)

        moments = next(t for t in result["tabs"] if t["component"] == "moment_track")
        thumbed = [it for it in moments["props"]["items"] if it.get("thumbnailUrl")]
        assert len(thumbed) == 1  # one frame can serve only one moment in the tab

    def test_helper_respects_15s_window_and_tab_dedup(self):
        from src.services.pipeline.assembly.core import _backfill_moment_thumbnails

        # 20s away — outside the relaxed ±15s window.
        far = [
            {
                "component": "moment_track",
                "props": {"items": [{"label": "far", "seconds": 120}]},
            }
        ]
        frames = [{"timestamp": 100.0, "s3_url": "https://s3/f100.jpg", "s3_key": "f100"}]
        _backfill_moment_thumbnails(far, frames, None, None)
        assert "thumbnailUrl" not in far[0]["props"]["items"][0]

        # Frame already used within the tab — never duplicated by backfill.
        dup = [
            {
                "component": "moment_track",
                "props": {
                    "items": [
                        {"label": "has", "seconds": 100, "thumbnailUrl": "u", "s3Key": "f100"},
                        {"label": "wants", "seconds": 108},
                    ]
                },
            }
        ]
        _backfill_moment_thumbnails(dup, frames, None, None)
        assert "thumbnailUrl" not in dup[0]["props"]["items"][1]


class TestFilmstripSuppressedByRichMomentGallery:
    def test_auto_gallery_skipped_when_moment_grid_is_rich(self):
        frames = [
            {
                "timestamp": float(i * 30 + 5),
                "s3_url": f"https://s3/f{i}.jpg",
                "s3_key": f"f{i}",
                "ocr_text": f"caption {i}",
            }
            for i in range(12)
        ]
        descriptions = [
            {"timestamp_sec": float(i * 30 + 5), "scene_type": "product_demo", "content": f"S{i}"}
            for i in range(12)
        ]
        triage = {
            "contentTags": ["gaming"],
            "primaryTag": "gaming",
            "userGoal": "g",
            "tabs": [
                {
                    "id": "pulls",
                    "label": "Pulls",
                    "emoji": "⭐",
                    "component": "tier_list",
                    "dataSource": "gaming.rankings",
                },
                {
                    "id": "highlights",
                    "label": "H",
                    "emoji": "🎬",
                    "component": "moment_track",
                    "dataSource": "gaming.highlights",
                },
            ],
        }
        extraction = {
            "gaming": {
                "rankings": [{"item": f"Card {i}", "tier": "A", "reason": "r"} for i in range(4)],
                # 10 moments right on the frame timestamps -> rich thumb coverage
                "highlights": [
                    {"title": f"Pull {i}", "timestamp": i * 30 + 5, "description": "d"}
                    for i in range(10)
                ],
            }
        }
        result = assemble_response(
            triage,
            extraction,
            None,
            None,
            frames=frames,
            gallery_frames=frames,
            frame_descriptions=descriptions,
        )

        moments = next(t for t in result["tabs"] if t["component"] == "moment_track")
        thumbed = sum(1 for it in moments["props"]["items"] if it.get("thumbnailUrl"))
        assert thumbed >= 8  # sanity: the gallery is genuinely rich
        assert not any(t["id"] == "frames-gallery" for t in result["tabs"])


class TestTimestampHygiene:
    """Beyond-duration moments are dropped; second-zero moments still match."""

    def test_drops_moments_beyond_video_duration(self):
        from src.services.pipeline.assembly.core import _drop_impossible_timestamps

        tabs = [
            {
                "component": "moment_track",
                "props": {
                    "items": [
                        {"label": "real", "seconds": 300},
                        {"label": "ghost", "seconds": 500},
                        {"label": "way-out", "seconds": 720},
                    ]
                },
            },
            # Non-moment tabs are out of scope for the drop pass.
            {"component": "tier_list", "props": {"items": [{"item": "A", "timestamp": 999}]}},
        ]

        dropped = _drop_impossible_timestamps(tabs, 470)

        assert dropped == 2
        labels = [it["label"] for it in tabs[0]["props"]["items"]]
        assert labels == ["real"]
        assert len(tabs[1]["props"]["items"]) == 1

    def test_unknown_duration_is_a_noop(self):
        from src.services.pipeline.assembly.core import _drop_impossible_timestamps

        tabs = [
            {
                "component": "moment_track",
                "props": {"items": [{"label": "m", "seconds": 9999}]},
            }
        ]

        assert _drop_impossible_timestamps(tabs, None) == 0
        assert _drop_impossible_timestamps(tabs, "12:34") == 0
        assert len(tabs[0]["props"]["items"]) == 1

    def test_second_zero_moment_still_matches_a_frame(self):
        """Regression: the old `a or b or c` timestamp chain made seconds=0
        fall through to the display string, so opening moments never matched."""
        from src.services.pipeline.assembly.core import inject_frame_thumbnails

        items = [{"label": "opening", "seconds": 0, "time": "0:00"}]
        frames = [{"timestamp": 1.0, "s3_url": "https://s3/f0.jpg", "s3_key": "f0"}]

        inject_frame_thumbnails(items, frames)

        assert items[0]["thumbnailUrl"] == "https://s3/f0.jpg"

    def test_label_only_moment_survives_the_drop_pass(self):
        """An item without any numeric timestamp has nothing to compare — keep it."""
        from src.services.pipeline.assembly.core import _drop_impossible_timestamps

        tabs = [
            {
                "component": "moment_track",
                "props": {
                    "items": [{"label": "no-ts", "time": "1:00"}, {"label": "x", "seconds": 50}]
                },
            }
        ]

        assert _drop_impossible_timestamps(tabs, 60) == 0
        assert len(tabs[0]["props"]["items"]) == 2

    def test_all_hallucinated_moments_drop_the_whole_tab(self):
        """Regression: the drop pass ran after props validation, so a tab whose
        every moment was beyond duration shipped as an empty surface."""
        from src.services.pipeline.assembly.core import (
            _drop_emptied_moment_tabs,
            _drop_impossible_timestamps,
        )

        tabs = [
            {"id": "overview", "component": "overview", "props": {"tldr": "x"}},
            {
                "id": "moments",
                "component": "moment_track",
                "props": {"items": [{"label": "ghost", "seconds": 500}]},
            },
            {
                "id": "moments_ok",
                "component": "moment_track",
                "props": {
                    "items": [{"label": "real", "seconds": 30}, {"label": "g", "seconds": 900}]
                },
            },
        ]
        dropped_sink: list[dict] = []

        assert _drop_impossible_timestamps(tabs, 470) == 2
        _drop_emptied_moment_tabs(tabs, dropped_sink)

        assert [t["id"] for t in tabs] == ["overview", "moments_ok"]
        assert dropped_sink == [
            {
                "id": "moments",
                "component": "moment_track",
                "dataSource": "",
                "reason": "all_timestamps_impossible",
            }
        ]

    def test_assembled_response_never_ships_an_empty_moment_tab(self):
        from src.services.pipeline.assembly.core import assemble_response

        triage = {
            "contentTags": ["podcast"],
            "modifiers": [],
            "primaryTag": "podcast",
            "userGoal": "Listen",
            "tabs": [
                {
                    "id": "moments",
                    "label": "Moments",
                    "emoji": "⏱️",
                    "dataSource": "podcast.highlights",
                    "component": "moment_track",
                    "goal": "Jump around",
                }
            ],
        }
        extraction = {
            "podcast": {
                "highlights": [
                    {"timestamp": "8:30", "seconds": 510, "description": "ghost one"},
                    {"timestamp": "12:00", "seconds": 720, "description": "ghost two"},
                ]
            }
        }

        result = assemble_response(
            triage, extraction, None, None, video_meta={"title": "t", "duration": 470}
        )

        moment_tabs = [t for t in result["tabs"] if t["component"] == "moment_track"]
        assert all(t["props"]["items"] for t in moment_tabs)
        assert any(d["reason"] == "all_timestamps_impossible" for d in result["dropped"])


class TestGenericListFallbackGuard:
    """The longest-list fallback must not turn a timeline into a reference grid."""

    def test_timeline_shaped_lists_are_skipped(self):
        from src.services.pipeline.assembly.core import _in_domain_sibling_fallback

        extraction = {
            "gaming": {
                "loadout": [],
                "highlights": [
                    {"timestamp": "1:00", "seconds": 60, "description": "clutch"},
                    {"timestamp": "2:00", "seconds": 120, "description": "ace"},
                    {"timestamp": "3:00", "seconds": 180, "description": "win"},
                ],
                "tips": [{"tip": "aim down sights"}, {"tip": "peek wide"}],
            }
        }

        value = _in_domain_sibling_fallback("gaming.loadout", "info_grid", extraction)

        assert value == extraction["gaming"]["tips"]

    def test_only_timeline_lists_means_no_fallback(self):
        from src.services.pipeline.assembly.core import _in_domain_sibling_fallback

        extraction = {
            "gaming": {
                "loadout": [],
                "highlights": [{"seconds": 60, "description": "clutch"}],
            }
        }

        assert _in_domain_sibling_fallback("gaming.loadout", "info_grid", extraction) is None
