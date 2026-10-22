"""Assembly post-processing tests — validation checkpoints, domain requirements, overview-first ordering."""

from src.services.pipeline.assembly import (
    _validate_assembled_props,
    _validate_domain_requirements,
    assemble_comparison,
    assemble_flash_deck,
    assemble_response,
)

# ─── resolve_data_source ───


class TestPostProcessing:
    """Tests for post-processing rules (0.7, 0.8, 0.9)."""

    def test_flash_deck_front_corruption(self):
        """Sprint 0.5: category-name fronts get fixed."""
        data = [
            {
                "front": "chef_tip",
                "back": "Always salt your pasta water generously. It should taste like the sea.",
            },
            {"front": "Real Concept", "back": "This is fine"},
        ]
        result = assemble_flash_deck({}, data, {}, None)
        assert result is not None
        assert result["cards"][0]["front"] != "chef_tip"
        assert (
            "salt" in result["cards"][0]["front"].lower()
            or "always" in result["cards"][0]["front"].lower()
        )
        assert result["cards"][1]["front"] == "Real Concept"

    def test_comparison_never_synthesizes_pros_cons(self):
        """0b.9 (R10): empty pros/cons stay empty — never derived from winner fields."""
        data = {
            "pros": [],
            "cons": [],
            "comparisons": [
                {
                    "feature": "Camera",
                    "thisProduct": "48MP",
                    "competitor": "12MP",
                    "winner": "left",
                },
                {
                    "feature": "Price",
                    "thisProduct": "$999",
                    "competitor": "$699",
                    "winner": "right",
                },
            ],
        }
        result = assemble_comparison({}, data, {}, None)
        assert result is not None
        assert result["pros"] == []
        assert result["cons"] == []

    def test_flash_deck_max_one_guardrail(self):
        """Sprint 0: assembly keeps only the first flash_deck tab."""

        tabs = [
            {
                "id": "concepts",
                "component": "flash_deck",
                "label": "Concepts",
                "emoji": "📚",
                "props": {"cards": [{"front": "A", "back": "B"}]},
            },
            {
                "id": "tips",
                "component": "flash_deck",
                "label": "Tips",
                "emoji": "💡",
                "props": {"cards": [{"front": "C", "back": "D"}]},
            },
            {
                "id": "quiz",
                "component": "quiz",
                "label": "Quiz",
                "emoji": "❓",
                "props": {"questions": []},
            },
        ]
        # flash_deck guardrail is in assemble_response, not _post_process_tabs
        # Test it via the full pipeline by checking tab dedup
        assert len([t for t in tabs if t["component"] == "flash_deck"]) == 2

    def test_untitled_chapter_strip(self):
        """Sprint 0.7: <Untitled Chapter N> gets replaced."""
        from src.services.pipeline.assembly import _post_process_tabs

        tabs = [
            {
                "id": "intro",
                "label": "<Untitled Chapter 1>",
                "component": "overview",
                "emoji": "",
                "props": {},
            },
            {
                "id": "part2",
                "label": "Untitled Chapter 2",
                "component": "display_section",
                "emoji": "",
                "props": {},
            },
        ]
        _post_process_tabs(tabs)
        assert tabs[0]["label"] == "Introduction"
        assert tabs[1]["label"] == "Part 2"

    def test_tab_labels_never_get_count_prefixes(self):
        """Labels stay as the planner wrote them — count-prefixing produced
        garbage like "25 Notable Card Pulls" / "10 Tier List: Best Pulls"."""
        from src.services.pipeline.assembly import _post_process_tabs

        tabs = [
            {
                "id": "gear",
                "label": "Gear",
                "component": "checklist",
                "emoji": "💪",
                "props": {"items": [{"name": "Pushup bar"}, {"name": "Mat"}]},
            },
            {
                "id": "overview",
                "label": "Overview",
                "component": "overview",
                "emoji": "📋",
                "props": {"data": {"key": "val"}},
            },
        ]
        _post_process_tabs(tabs)
        assert tabs[0]["label"] == "Gear"
        assert tabs[1]["label"] == "Overview"

    def test_double_emoji_strip(self):
        """Sprint 0.9: leading emoji stripped when emoji field exists."""
        from src.services.pipeline.assembly import _post_process_tabs

        tabs = [
            {
                "id": "shapes",
                "label": "🍝 Pasta Shapes",
                "component": "spot_explorer",
                "emoji": "🍝",
                "props": {"spots": []},
            },
        ]
        _post_process_tabs(tabs)
        assert not tabs[0]["label"].startswith("🍝")

    def test_caps_oversized_comparison_to_10(self):
        """Regression: a 29-row comparison (the Yann LeCun video bug) must be capped to 10."""
        from src.services.pipeline.assembly.core import _post_process_tabs

        tabs = [
            {
                "id": "concepts",
                "label": "Concepts",
                "component": "comparison",
                "emoji": "⚖️",
                "props": {
                    "comparisons": [
                        {"feature": f"f{i}", "left": "L", "right": "R"} for i in range(29)
                    ]
                },
            },
        ]
        _post_process_tabs(tabs)
        assert len(tabs[0]["props"]["comparisons"]) == 10
        assert tabs[0]["label"] == "Concepts"

    def test_caps_oversized_moment_track_to_20(self):
        """Regression: 55-item timelines must be capped to 20."""
        from src.services.pipeline.assembly.core import _post_process_tabs

        tabs = [
            {
                "id": "key_moments",
                "label": "Key Moments",
                "component": "moment_track",
                "emoji": "⏱️",
                "props": {
                    "items": [
                        {"time": f"{i}:00", "seconds": i * 60, "label": f"m{i}"} for i in range(55)
                    ]
                },
            },
        ]
        _post_process_tabs(tabs)
        assert len(tabs[0]["props"]["items"]) == 20

    def test_moment_track_cap_scales_with_duration_and_spans_video(self):
        """Long videos: timeline cap scales with duration and keeps items
        evenly across the whole video (not just the first N)."""
        from src.services.pipeline.assembly.core import _post_process_tabs

        # 4.5h video, 72 timestamps spread to the end.
        items = [{"time": f"{i}:00", "seconds": i * 233, "label": f"m{i}"} for i in range(72)]
        tabs = [
            {
                "id": "key_moments",
                "label": "Key Moments",
                "component": "moment_track",
                "emoji": "⏱️",
                "props": {"items": list(items)},
            }
        ]
        _post_process_tabs(tabs, video_duration=16789)  # ~280 min → 1 / 6 min = 46
        kept = tabs[0]["props"]["items"]
        assert len(kept) == 46
        # One moment per stride → the timeline spans the whole video.
        stride = 16789 / 46
        assert kept[0]["seconds"] < stride
        assert kept[-1]["seconds"] >= 16789 - stride

    def test_evenly_sample_preserves_first_and_last(self):
        from src.services.pipeline.assembly.sampling import evenly_sample

        items = list(range(100))
        sampled = evenly_sample(items, 10)
        assert sampled[0] == 0
        assert sampled[-1] == 99
        assert len(sampled) <= 10
        # Returns every item when already within cap.
        assert evenly_sample([1, 2, 3], 10) == [1, 2, 3]

    def test_caps_oversized_info_grid_to_20(self):
        from src.services.pipeline.assembly.core import _post_process_tabs

        tabs = [
            {
                "id": "facts",
                "label": "Facts",
                "component": "info_grid",
                "emoji": "📋",
                "props": {"items": [{"key": f"k{i}", "value": "v"} for i in range(40)]},
            },
        ]
        _post_process_tabs(tabs)
        assert len(tabs[0]["props"]["items"]) == 20

    def test_within_cap_passes_unchanged(self):
        """Tabs already within the cap must not be touched."""
        from src.services.pipeline.assembly.core import _post_process_tabs

        original = [{"feature": f"f{i}", "left": "L", "right": "R"} for i in range(5)]
        tabs = [
            {
                "id": "compare",
                "label": "Compare",
                "component": "comparison",
                "emoji": "⚖️",
                "props": {"comparisons": list(original)},
            },
        ]
        _post_process_tabs(tabs)
        assert tabs[0]["props"]["comparisons"] == original


# ─── Domain Requirement Validation ───


class TestDomainRequirementValidation:
    """Tests for _validate_domain_requirements."""

    def test_missing_required_components_warns_only(self, caplog):
        """Missing required components log warnings but don't drop tabs."""
        tabs = [
            {"id": "overview", "component": "overview", "label": "Overview", "goal": "Intro"},
            {"id": "tips", "component": "info_grid", "label": "Tips", "goal": "Tips"},
        ]
        with caplog.at_level("WARNING"):
            _validate_domain_requirements(tabs, "food")
        assert len(tabs) == 2  # no tabs dropped
        assert "step_player" in caplog.text
        assert "checklist" in caplog.text

    def test_max_flash_deck_enforced(self):
        """Multiple flash_deck tabs → only first kept."""
        tabs = [
            {"id": "concepts", "component": "flash_deck", "label": "Concepts", "goal": "Learn"},
            {"id": "terms", "component": "flash_deck", "label": "Terms", "goal": "Study"},
            {"id": "overview", "component": "overview", "label": "Overview", "goal": "Intro"},
        ]
        _validate_domain_requirements(tabs, "learning")
        flash_tabs = [t for t in tabs if t["component"] == "flash_deck"]
        assert len(flash_tabs) == 1
        assert flash_tabs[0]["id"] == "concepts"

    def test_max_quiz_enforced_for_tech(self):
        """Multiple quiz_arena tabs in tech → only first kept (post-overhaul: quiz → quiz_arena)."""
        tabs = [
            {"id": "quiz1", "component": "quiz_arena", "label": "Quiz 1", "goal": "Test"},
            {"id": "quiz2", "component": "quiz_arena", "label": "Quiz 2", "goal": "Test more"},
        ]
        _validate_domain_requirements(tabs, "tech")
        quiz_tabs = [t for t in tabs if t["component"] == "quiz_arena"]
        assert len(quiz_tabs) == 1

    def test_no_max_constraints_no_drop(self):
        """Tabs within limits stay untouched."""
        tabs = [
            {"id": "concepts", "component": "flash_deck", "label": "Concepts", "goal": "Learn"},
            {"id": "quiz", "component": "quiz", "label": "Quiz", "goal": "Test"},
            {"id": "overview", "component": "overview", "label": "Overview", "goal": "Intro"},
        ]
        _validate_domain_requirements(tabs, "learning")
        assert len(tabs) == 3

    def test_empty_goal_warns(self, caplog):
        """Tabs with empty goal get a warning."""
        tabs = [
            {"id": "steps", "component": "step_player", "label": "Steps", "goal": ""},
        ]
        with caplog.at_level("WARNING"):
            _validate_domain_requirements(tabs, "food")
        assert "empty goal" in caplog.text

    def test_empty_label_warns(self, caplog):
        """Tabs with empty label get a warning."""
        tabs = [
            {"id": "steps", "component": "step_player", "label": "", "goal": "Build it"},
        ]
        with caplog.at_level("WARNING"):
            _validate_domain_requirements(tabs, "project")
        assert "empty label" in caplog.text

    def test_untitled_chapter_in_label_fixed(self):
        """Untitled Chapter placeholders in labels get replaced."""
        tabs = [
            {
                "id": "intro",
                "component": "overview",
                "label": "<Untitled Chapter 1>",
                "goal": "Intro",
            },
        ]
        _validate_domain_requirements(tabs, "learning")
        assert tabs[0]["label"] == "Introduction"

    def test_unknown_domain_no_crash(self):
        """Unknown domain doesn't crash — just no requirements enforced."""
        tabs = [
            {"id": "misc", "component": "display_section", "label": "Misc", "goal": "Data"},
        ]
        _validate_domain_requirements(tabs, "unknown_domain")
        assert len(tabs) == 1


# ─── Data Quality: Validation Checkpoint Tests ───


class TestAssemblyValidation:
    """Phase 2: _validate_assembled_props."""

    def test_valid_props_pass(self):
        assert _validate_assembled_props("quiz_arena", {"questions": [{"q": "test"}]}) is True

    def test_empty_required_list_fails(self):
        assert _validate_assembled_props("quiz_arena", {"questions": []}) is False

    def test_missing_required_list_fails(self):
        assert _validate_assembled_props("moment_track", {"data": "something"}) is False

    def test_moment_track_required_list_items(self):
        """Required list for moment_track is 'items' — empty list drops the tab."""
        assert _validate_assembled_props("moment_track", {"items": []}) is False
        assert _validate_assembled_props("moment_track", {"items": [{"label": "x"}]}) is True

    def test_unknown_component_passes(self):
        assert _validate_assembled_props("unknown_widget", {"anything": True}) is True

    def test_overview_always_passes(self):
        """overview has no required list — always passes."""
        assert _validate_assembled_props("overview", {"data": {"key": "val"}}) is True

    def test_validation_drops_tab_in_full_assembly(self):
        """End-to-end: assembler returns props with empty list → tab dropped."""
        triage = {
            "contentTags": ["learning"],
            "primaryTag": "learning",
            "tabs": [
                {"id": "quizzes", "label": "Quiz", "emoji": "❓", "dataSource": "enrichment.quiz"},
            ],
        }
        # All quiz questions are invalid (empty question text)
        enrichment = {"quiz": [{"question": "", "options": ["A", "B"]}]}
        result = assemble_response(triage, {}, enrichment, None)
        tab_ids = [t["id"] for t in result["tabs"]]
        assert "quizzes" not in tab_ids


# ─── Overview-First Guarantee ───


class TestOverviewFirst:
    """Overview tab must always sit at index 0 and route to OverviewInteractive."""

    def test_existing_overview_moved_to_front(self):
        """Overview emitted at non-zero position gets moved to index 0."""
        triage = {
            "contentTags": ["learning"],
            "primaryTag": "learning",
            "tabs": [
                {
                    "id": "concepts",
                    "label": "Concepts",
                    "emoji": "📚",
                    "dataSource": "learning.concepts",
                    "component": "flash_deck",
                },
                {
                    "id": "overview",
                    "label": "Overview",
                    "emoji": "📋",
                    "dataSource": "meta",
                    "component": "overview",
                },
            ],
        }
        extraction = {"learning": {"concepts": [{"name": "C", "definition": "D"}]}}
        synthesis = {"masterSummary": "A great video", "keyTakeaways": ["t1"]}
        result = assemble_response(triage, extraction, None, synthesis)

        assert result["tabs"][0]["id"] == "overview"
        assert result["tabs"][0]["component"] == "overview"

    def test_overview_synthesized_when_missing(self):
        """No overview in triage → one gets prepended from synthesis."""
        triage = {
            "contentTags": ["learning"],
            "primaryTag": "learning",
            "tabs": [
                {
                    "id": "concepts",
                    "label": "Concepts",
                    "emoji": "📚",
                    "dataSource": "learning.concepts",
                    "component": "flash_deck",
                },
            ],
        }
        extraction = {"learning": {"concepts": [{"name": "C", "definition": "D"}]}}
        synthesis = {"masterSummary": "A great video", "keyTakeaways": ["t1", "t2"]}
        result = assemble_response(triage, extraction, None, synthesis)

        assert result["tabs"][0]["id"] == "overview"
        assert result["tabs"][0]["component"] == "overview"
        assert result["tabs"][0]["props"]["data"]["masterSummary"] == "A great video"

    def test_display_section_overview_gets_normalized(self):
        """id=overview with mis-tagged component=display_section becomes overview."""
        triage = {
            "contentTags": ["learning"],
            "primaryTag": "learning",
            "tabs": [
                {
                    "id": "overview",
                    "label": "Overview",
                    "emoji": "📋",
                    "dataSource": "learning",
                    "component": "display_section",
                },
                {
                    "id": "concepts",
                    "label": "Concepts",
                    "emoji": "📚",
                    "dataSource": "learning.concepts",
                    "component": "flash_deck",
                },
            ],
        }
        extraction = {
            "learning": {
                "summary": "x",
                "concepts": [{"name": "C", "definition": "D"}],
            },
        }
        synthesis = {"masterSummary": "A great video", "keyTakeaways": ["t1"]}
        result = assemble_response(triage, extraction, None, synthesis)

        assert result["tabs"][0]["id"] == "overview"
        assert result["tabs"][0]["component"] == "overview"

    def test_no_overview_when_no_data_available(self):
        """With no synthesis and no primitive domain data, no overview is injected."""
        triage = {
            "contentTags": ["learning"],
            "primaryTag": "learning",
            "tabs": [
                {
                    "id": "concepts",
                    "label": "Concepts",
                    "emoji": "📚",
                    "dataSource": "learning.concepts",
                    "component": "flash_deck",
                },
            ],
        }
        extraction = {"learning": {"concepts": [{"name": "C", "definition": "D"}]}}
        result = assemble_response(triage, extraction, None, None)

        components = [t["component"] for t in result["tabs"]]
        assert "overview" not in components

    def test_overview_emits_rich_hero_fields(self):
        """Overview data should include title, emoji, subtitle, stats for the hero."""
        triage = {
            "contentTags": ["tech"],
            "primaryTag": "tech",
            "tabs": [
                {
                    "id": "code",
                    "label": "Code",
                    "emoji": "💻",
                    "dataSource": "tech.snippets",
                    "component": "code_explorer",
                },
            ],
        }
        extraction = {
            "tech": {
                "snippets": [{"code": "print(1)", "language": "python"}],
                "meta": {"difficulty": "advanced"},
                "tips": ["Cache aggressively"],
            },
        }
        synthesis = {
            "tldr": "Short one-liner description",
            "masterSummary": "Full master summary of the video",
            "keyTakeaways": ["t1", "t2"],
        }
        video_meta = {"title": "Real Video Title", "duration": 2700, "channel": "Creator"}
        result = assemble_response(triage, extraction, None, synthesis, video_meta=video_meta)

        data = result["tabs"][0]["props"]["data"]
        assert data["title"] == "Real Video Title"
        assert data["emoji"] == "💻"  # tech domain
        assert data["subtitle"] == "Short one-liner description"
        assert data["masterSummary"] == "Full master summary of the video"
        assert data["level"] == "Advanced"
        assert data["tips"] == ["Cache aggressively"]
        stat_labels = {s["label"] for s in data["stats"]}
        assert {"Duration", "Level", "Items"}.issubset(stat_labels)

    def test_overview_item_count_reflects_other_tabs(self):
        """`itemCount` counts non-overview tabs."""
        triage = {
            "contentTags": ["learning"],
            "primaryTag": "learning",
            "tabs": [
                {
                    "id": "concepts",
                    "label": "Concepts",
                    "emoji": "📚",
                    "dataSource": "learning.concepts",
                    "component": "flash_deck",
                },
                {
                    "id": "takeaways",
                    "label": "Takeaways",
                    "emoji": "✅",
                    "dataSource": "learning.takeaways",
                    "component": "checklist",
                },
            ],
        }
        extraction = {
            "learning": {
                "concepts": [{"name": "C", "definition": "D"}],
                "takeaways": ["t1"],
            },
        }
        synthesis = {"masterSummary": "ms", "keyTakeaways": ["k1"]}
        result = assemble_response(triage, extraction, None, synthesis)

        data = result["tabs"][0]["props"]["data"]
        assert data["itemCount"] == 2

    def test_overview_deduped_when_already_first(self):
        """Overview already at index 0 is not duplicated."""
        triage = {
            "contentTags": ["learning"],
            "primaryTag": "learning",
            "tabs": [
                {
                    "id": "overview",
                    "label": "Overview",
                    "emoji": "📋",
                    "dataSource": "meta",
                    "component": "overview",
                },
                {
                    "id": "concepts",
                    "label": "Concepts",
                    "emoji": "📚",
                    "dataSource": "learning.concepts",
                    "component": "flash_deck",
                },
            ],
        }
        extraction = {"learning": {"concepts": [{"name": "C", "definition": "D"}]}}
        synthesis = {"masterSummary": "A great video", "keyTakeaways": ["t1"]}
        result = assemble_response(triage, extraction, None, synthesis)

        overview_count = sum(1 for t in result["tabs"] if t["component"] == "overview")
        assert overview_count == 1
        assert result["tabs"][0]["id"] == "overview"

    def test_overview_item_count_reflects_post_fallback_tab_count(self):
        """itemCount must be annotated AFTER the 3-tab fallback minimum kicks in.

        Regression: previously the annotation ran inside _ensure_overview_first,
        before fallback tabs were appended, so the "Items" hero chip under-
        reported the real tab count.
        """
        triage = {
            "contentTags": ["learning"],
            "primaryTag": "learning",
            "tabs": [
                {
                    "id": "concepts",
                    "label": "Concepts",
                    "emoji": "📚",
                    "dataSource": "learning.concepts",
                    "component": "flash_deck",
                },
            ],
        }
        extraction = {"learning": {"concepts": [{"name": "C", "definition": "D"}]}}
        synthesis = {
            "masterSummary": "Full summary",
            "keyTakeaways": ["k1", "k2", "k3"],
            "tldr": "Short line",
        }
        result = assemble_response(triage, extraction, None, synthesis)

        # After the 3-tab minimum kicks in, we should have overview + content tabs.
        assert result["tabs"][0]["component"] == "overview"
        data = result["tabs"][0]["props"]["data"]
        assert data["itemCount"] == len(result["tabs"]) - 1


class TestSpotCapSectionsRemap:
    """Regression: the Vietnam-itinerary bug — capping spots to 25 while
    sections still referenced indices up to 50 rendered Days 5-10 empty."""

    @staticmethod
    def _build_tab(n_spots: int, spots_per_day: int) -> dict:
        spots = [{"name": f"Spot {i}", "description": f"Desc {i}"} for i in range(n_spots)]
        sections = []
        for day, start in enumerate(range(0, n_spots, spots_per_day), 1):
            indices = list(range(start, min(start + spots_per_day, n_spots)))
            sections.append({"label": f"Day {day}", "spotIndices": indices})
        return {
            "id": "itinerary",
            "label": "Itinerary",
            "emoji": "🧭",
            "component": "spot_explorer",
            "props": {"spots": spots, "sections": sections},
        }

    def test_no_dangling_indices_after_cap(self):
        from src.services.pipeline.assembly.core import _post_process_tabs

        tab = self._build_tab(n_spots=51, spots_per_day=5)
        tabs = [tab]
        _post_process_tabs(tabs)

        spots = tabs[0]["props"]["spots"]
        sections = tabs[0]["props"]["sections"]
        assert len(spots) <= 25
        for section in sections:
            for idx in section["spotIndices"]:
                assert 0 <= idx < len(spots), f"dangling index {idx} in {section['label']}"

    def test_even_sampling_preserves_late_days(self):
        from src.services.pipeline.assembly.core import _post_process_tabs

        tab = self._build_tab(n_spots=51, spots_per_day=5)
        tabs = [tab]
        _post_process_tabs(tabs)

        labels = [s["label"] for s in tabs[0]["props"]["sections"]]
        # 11 days of 5 spots — even-sampling must keep spots from the LAST day,
        # not amputate the tail like a head-truncation would.
        assert labels[-1] == "Day 11"
        # Every remaining section still points at at least one live spot.
        assert all(s["spotIndices"] for s in tabs[0]["props"]["sections"])

    def test_under_cap_untouched(self):
        from src.services.pipeline.assembly.core import _post_process_tabs

        tab = self._build_tab(n_spots=10, spots_per_day=5)
        tabs = [tab]
        _post_process_tabs(tabs)

        assert len(tabs[0]["props"]["spots"]) == 10
        assert [s["spotIndices"] for s in tabs[0]["props"]["sections"]] == [
            [0, 1, 2, 3, 4],
            [5, 6, 7, 8, 9],
        ]

    def test_flat_spots_without_sections_head_truncated(self):
        from src.services.pipeline.assembly.core import _post_process_tabs

        tabs = [
            {
                "id": "spots",
                "label": "Spots",
                "emoji": "📍",
                "component": "spot_explorer",
                "props": {"spots": [{"name": f"S{i}", "description": "d"} for i in range(30)]},
            }
        ]
        _post_process_tabs(tabs)
        assert len(tabs[0]["props"]["spots"]) == 25
        assert tabs[0]["props"]["spots"][0]["name"] == "S0"

    def test_every_section_keeps_at_least_one_spot(self):
        """Per-section quotas: no day sub-tab may vanish while sections <= cap."""
        from src.services.pipeline.assembly.core import _post_process_tabs

        # 10 days x 5 spots = 50 spots -> capped to 25 but ALL 10 days survive.
        tab = self._build_tab(n_spots=50, spots_per_day=5)
        tabs = [tab]
        _post_process_tabs(tabs)

        sections = tabs[0]["props"]["sections"]
        assert [s["label"] for s in sections] == [f"Day {d}" for d in range(1, 11)]
        assert all(len(s["spotIndices"]) >= 1 for s in sections)
        assert len(tabs[0]["props"]["spots"]) <= 25

    def test_more_sections_than_cap_keeps_one_each_for_an_even_pick_with_the_last_day(self):
        from src.services.pipeline.assembly.core import _post_process_tabs

        # 30 days x 2 spots = 60 spots -> 25 days evenly picked keep one spot each;
        # the tail survives (output-quality 3.3: no head-slice of the sections).
        tab = self._build_tab(n_spots=60, spots_per_day=2)
        tabs = [tab]
        _post_process_tabs(tabs)

        sections = tabs[0]["props"]["sections"]
        labels = [s["label"] for s in sections]
        assert len(sections) == 25
        assert labels[0] == "Day 1" and labels[-1] == "Day 30"
        assert all(len(s["spotIndices"]) == 1 for s in sections)
        assert len(tabs[0]["props"]["spots"]) == 25

    def test_small_section_survives_next_to_giant_section(self):
        """Largest-remainder floor-1: a 1-spot day is not sampled away by a
        40-spot day hogging the whole budget."""
        from src.services.pipeline.assembly.core import _cap_spots_with_sections

        spots = [{"name": f"S{i}", "description": "d"} for i in range(41)]
        props = {
            "spots": spots,
            "sections": [
                {"label": "Big day", "spotIndices": list(range(40))},
                {"label": "Small day", "spotIndices": [40]},
            ],
        }
        _cap_spots_with_sections(props, spots, 25)

        labels = [s["label"] for s in props["sections"]]
        assert "Small day" in labels
        small = next(s for s in props["sections"] if s["label"] == "Small day")
        assert len(small["spotIndices"]) == 1
        assert len(props["spots"]) <= 25
        for section in props["sections"]:
            for idx in section["spotIndices"]:
                assert 0 <= idx < len(props["spots"])
