"""Assembler unit tests — primary family (spots, moments, comparison, info_grid, checklist, steps, code)."""

from src.services.pipeline.assembly import (
    _normalize_code_snippet,
    _normalize_moment_item,
    assemble_checklist,
    assemble_code_explorer,
    assemble_comparison,
    assemble_info_grid,
    assemble_moment_track,
    assemble_spot_explorer,
)

# ─── resolve_data_source ───


# ─── Assemblers ───


class TestAssembleSpotExplorer:
    def test_travel_days(self):
        data = [
            {
                "day": 1,
                "city": "Tokyo",
                "spots": [
                    {"name": "Shibuya", "description": "Iconic scramble crossing"},
                    {"name": "Harajuku", "description": "Youth fashion district"},
                ],
            },
            {
                "day": 2,
                "city": "Osaka",
                "spots": [
                    {"name": "Dotonbori", "description": "Neon-lit canal nightlife"},
                ],
            },
        ]
        result = assemble_spot_explorer({}, data, {}, None)
        assert result is not None
        assert len(result["spots"]) == 3
        assert len(result["sections"]) == 2
        assert result["sections"][0]["label"] == "Day 1: Tokyo"
        assert result["sections"][0]["spotIndices"] == [0, 1]
        assert result["sections"][1]["spotIndices"] == [2]

    def test_flat_spots(self):
        data = [
            {"name": "Spot A", "description": "First stop"},
            {"name": "Spot B", "description": "Second stop"},
        ]
        result = assemble_spot_explorer({}, data, {}, None)
        assert result is not None
        assert len(result["spots"]) == 2
        assert "sections" not in result

    def test_empty(self):
        assert assemble_spot_explorer({}, [], {}, None) is None
        assert assemble_spot_explorer({}, None, {}, None) is None

    def test_name_only_spots_dropped(self):
        # A spot needs at least a description or a passthrough field — bare
        # name dicts render as empty cards in spot_explorer and are usually a
        # symptom of cross-domain keyword pollution. Pin the behavior so a
        # future contributor doesn't loosen the rule by accident.
        data = [{"name": "Spot A"}, {"name": "Spot B"}]
        assert assemble_spot_explorer({}, data, {}, None) is None

    def test_name_with_passthrough_kept(self):
        # Passthrough fields (emoji, cost, duration, …) count as descriptive
        # content, so name + emoji is enough to render a real card.
        data = [
            {"name": "Spot A", "emoji": "🍕"},
            {"name": "Spot B", "emoji": "🍣"},
        ]
        result = assemble_spot_explorer({}, data, {}, None)
        assert result is not None
        assert len(result["spots"]) == 2
        assert result["spots"][0]["emoji"] == "🍕"

    def test_string_items_dropped(self):
        # String items used to be promoted to {name: "...", description: ""};
        # now they're rejected because extraction should produce structured
        # dicts. Below the _MIN_SPOTS threshold ⇒ tab is dropped entirely.
        data = ["Spot A", "Spot B"]
        assert assemble_spot_explorer({}, data, {}, None) is None

    def test_below_min_spots_dropped(self):
        # _MIN_SPOTS = 2 — single-spot tabs degrade poorly in the explorer UI,
        # so the fallback layer takes over (overview/info_grid).
        data = [{"name": "Lone Spot", "description": "Only one"}]
        assert assemble_spot_explorer({}, data, {}, None) is None


class TestAssembleChecklist:
    def test_food_ingredients(self):
        data = [
            {"name": "flour", "amount": 2, "unit": "cups", "displayAmount": "2 cups"},
            {"name": "salt", "amount": 0, "displayAmount": "to taste"},
        ]
        result = assemble_checklist({"id": "ingredients"}, data, {}, None)
        assert result is not None
        # tabLabel is no longer injected by the backend — the FE derives the
        # section heading from the (translated) tab.label. Only the items
        # contract matters here.
        assert result["items"][0]["label"] == "2 cups flour"
        assert result["items"][1]["label"] == "to taste salt"

    def test_packing_items(self):
        data = [{"item": "Passport", "category": "Documents", "essential": True}]
        result = assemble_checklist({"id": "packing"}, data, {}, None)
        assert result is not None
        assert result["items"][0]["label"] == "Passport"
        assert result["items"][0]["emoji"] == "⚠️"

    def test_string_items(self):
        data = ["item 1", "item 2"]
        result = assemble_checklist({"id": "tools"}, data, {}, None)
        assert result is not None
        assert result["items"][0]["label"] == "item 1"

    def test_empty(self):
        assert assemble_checklist({}, [], {}, None) is None


class TestAssembleComparison:
    def test_pros_cons(self):
        data = {"pros": ["Fast"], "cons": ["Expensive"], "comparisons": []}
        result = assemble_comparison({}, data, {}, None)
        assert result is not None
        assert result["pros"] == ["Fast"]
        assert result["cons"] == ["Expensive"]

    def test_empty(self):
        assert (
            assemble_comparison({}, {"pros": [], "cons": [], "comparisons": []}, {}, None) is None
        )

    def test_non_dict_non_list_returns_none(self):
        assert assemble_comparison({}, "not a dict", {}, None) is None
        assert assemble_comparison({}, 42, {}, None) is None

    def test_cheat_sheet_list_no_longer_coerced(self):
        # Cheat-sheet shape {title, description, code} no longer silently coerces
        # to comparison rows — that produced the audited "video title as column
        # header / empty third column" rendering on review videos. The data
        # belongs in code_explorer or info_grid.
        data = [
            {
                "title": "useState",
                "description": "Manages local state",
                "code": "const [s, setS] = useState(0)",
            },
            {
                "title": "useEffect",
                "description": "Runs side effects",
                "code": "useEffect(() => {}, [])",
            },
        ]
        assert assemble_comparison({}, data, {}, None) is None

    def test_concepts_list_no_longer_coerced(self):
        # Concept shape {name, definition, example} routes to flash_deck or
        # info_grid now, not comparison. Single-sided definitions were the
        # source of the audited "8 empty cards" failure mode.
        data = [
            {
                "name": "MCP",
                "emoji": "🔌",
                "definition": "Model Context Protocol",
                "example": "LLM tool calls",
            },
            {
                "name": "Skills",
                "emoji": "🧠",
                "definition": "Predefined agent behaviors",
                "analogy": "Function library",
            },
        ]
        assert assemble_comparison({}, data, {}, None) is None

    def test_key_points_list_no_longer_coerced(self):
        # Key-points shape {title, detail} is single-sided — not a comparison.
        # The new contract requires both `thisProduct` and `competitor` filled
        # on each row before the row counts as a real pair.
        data = [
            {"emoji": "⚡", "title": "Speed", "detail": "MCP is faster for tool execution"},
            {"emoji": "🔧", "title": "Flexibility", "detail": "Skills are more reusable"},
        ]
        assert assemble_comparison({}, data, {}, None) is None

    def test_real_comparison_pair_accepted(self):
        # Properly-shaped comparison data DOES go through.
        data = [
            {
                "feature": "Battery",
                "thisProduct": "12 hours",
                "competitor": "8 hours",
                "winner": "left",
            },
            {"feature": "Price", "thisProduct": "$1299", "competitor": "$999", "winner": "right"},
            {"feature": "Weight", "thisProduct": "1.4kg", "competitor": "1.6kg", "winner": "left"},
        ]
        result = assemble_comparison({}, data, {"review": {"product": "Laptop A"}}, None)
        assert result is not None
        assert len(result["comparisons"]) == 3
        assert result["leftLabel"] == "Laptop A"

    def test_numeric_zero_sides_preserved(self):
        """Numeric 0 / False are legitimate content (free tier price, "no" on a
        feature row). The prior `or ""` truthiness check dropped these — they
        must survive the real-pair filter.
        """
        data = [
            # $0 free-tier vs $19 paid — a real and useful comparison.
            {"feature": "Monthly price", "thisProduct": 0, "competitor": 19},
            # Boolean feature comparison.
            {"feature": "Offline mode", "thisProduct": False, "competitor": True},
            # Real-pair string row to satisfy the 2-row minimum threshold even
            # if the numeric/bool rows were ever to drop.
            {"feature": "Storage", "thisProduct": "100 GB", "competitor": "50 GB"},
        ]
        result = assemble_comparison({}, data, {}, None)
        assert result is not None
        # All three rows must survive — none of them are "single-sided".
        features = [r["feature"] for r in result["comparisons"]]
        assert "Monthly price" in features
        assert "Offline mode" in features
        assert "Storage" in features

    def test_single_sided_rows_dropped_but_pros_cons_kept(self):
        # A comparison dict with pros/cons but no real-pair rows should still
        # surface — the frontend renders the pros/cons section even when the
        # main table is empty.
        data = {
            "pros": ["Fast"],
            "cons": ["Expensive"],
            "comparisons": [
                {"feature": "Speed", "thisProduct": "Fast", "competitor": ""},
            ],
        }
        result = assemble_comparison({}, data, {}, None)
        assert result is not None
        assert result["pros"] == ["Fast"]
        assert result["comparisons"] == []

    def test_empty_list_returns_none(self):
        assert assemble_comparison({}, [], {}, None) is None

    def test_list_with_no_matching_shape_returns_none(self):
        data = [{"unknown_key": "value"}, {"another": "thing"}]
        assert assemble_comparison({}, data, {}, None) is None


class TestAssembleMomentTrackHighlight:
    """assemble_moment_track with clip-style input (mood + description)."""

    def test_with_moments(self):
        data = [
            {
                "label": "Best play",
                "timestamp": 120,
                "mood": "exciting",
                "description": "Amazing goal",
                "endTimestamp": 180,
            },
            {"title": "Funny moment", "timestamp": 300},
        ]
        result = assemble_moment_track({}, data, {}, None)
        assert result is not None
        assert len(result["items"]) == 2
        assert result["items"][0]["label"] == "Best play"
        assert result["items"][0]["mood"] == "exciting"
        assert result["items"][0]["endSeconds"] == 180
        assert result["items"][1]["label"] == "Funny moment"
        assert "endSeconds" not in result["items"][1]

    def test_empty(self):
        assert assemble_moment_track({}, [], {}, None) is None
        assert assemble_moment_track({}, None, {}, None) is None


# ─── Flexible Assembler Tests ───


class TestFlexSpotExplorer:
    """Tests for cross-domain spot_explorer input shapes."""

    def test_name_description_shape(self):
        data = [
            {"name": "React Hooks", "description": "State management primitive", "emoji": "⚛️"},
            {"name": "useEffect", "description": "Side-effect scheduling", "emoji": "⚛️"},
        ]
        result = assemble_spot_explorer({}, data, {}, None)
        assert result is not None
        assert len(result["spots"]) == 2
        assert result["spots"][0]["name"] == "React Hooks"
        assert result["spots"][0]["description"] == "State management primitive"

    def test_aspect_detail_shape(self):
        data = [
            {"aspect": "Melody", "emoji": "🎵", "detail": "Uses pentatonic scale"},
            {"aspect": "Harmony", "emoji": "🎼", "detail": "Stacked thirds over I-IV-V"},
        ]
        result = assemble_spot_explorer({}, data, {}, None)
        assert result is not None
        assert result["spots"][0]["name"] == "Melody"
        assert result["spots"][0]["description"] == "Uses pentatonic scale"

    def test_label_explanation_shape(self):
        data = [
            {"label": "Cognitive Bias", "explanation": "Systematic error in thinking"},
            {"label": "Anchoring", "explanation": "Over-reliance on first piece of information"},
        ]
        result = assemble_spot_explorer({}, data, {}, None)
        assert result is not None
        assert result["spots"][0]["name"] == "Cognitive Bias"
        assert result["spots"][0]["description"] == "Systematic error in thinking"

    def test_empty_list_returns_none(self):
        assert assemble_spot_explorer({}, [], {}, None) is None

    def test_passthrough_fields(self):
        data = [
            {"name": "Place", "description": "Nice", "cost": "$50", "mapQuery": "place+near+me"},
            {"name": "Spot B", "description": "Also nice", "cost": "$30"},
        ]
        result = assemble_spot_explorer({}, data, {}, None)
        assert result is not None
        assert result["spots"][0]["cost"] == "$50"
        assert result["spots"][0]["mapQuery"] == "place+near+me"

    def test_language_phrase_shape(self):
        """Language phrases: {phrase, translation, pronunciation, context, timestamp}."""
        data = [
            {
                "phrase": "What attracted you?",
                "translation": "Interview question",
                "pronunciation": "wʌt əˈtræktɪd",
                "context": "Opening question",
                "timestamp": 85,
            },
            {
                "phrase": "To be honest",
                "translation": "Honesty marker",
                "context": "Transition phrase",
            },
        ]
        result = assemble_spot_explorer({}, data, {}, None)
        assert result is not None
        assert len(result["spots"]) == 2
        assert result["spots"][0]["name"] == "What attracted you?"
        assert result["spots"][0]["description"] == "Interview question"
        assert result["spots"][0]["pronunciation"] == "wʌt əˈtræktɪd"
        assert result["spots"][0]["timestamp"] == 85

    def test_language_vocabulary_shape(self):
        """Language vocabulary: {word, definition, pronunciation, partOfSpeech, example}."""
        data = [
            {
                "word": "ambitious",
                "definition": "Having a strong desire to succeed",
                "pronunciation": "æmˈbɪʃəs",
                "partOfSpeech": "adjective",
                "example": "ambitious goals",
            },
            {
                "word": "fluent",
                "definition": "Able to express oneself easily",
                "pronunciation": "ˈfluːənt",
                "partOfSpeech": "adjective",
            },
        ]
        result = assemble_spot_explorer({}, data, {}, None)
        assert result is not None
        assert result["spots"][0]["name"] == "ambitious"
        assert result["spots"][0]["description"] == "Having a strong desire to succeed"


class TestFlexChecklist:
    """Tests for cross-domain checklist input shapes."""

    def test_text_field_shape(self):
        data = [{"text": "Install Node.js", "notes": "v18+"}]
        result = assemble_checklist({"id": "prereqs"}, data, {}, None)
        assert result is not None
        assert result["items"][0]["label"] == "Install Node.js"
        assert result["items"][0]["note"] == "v18+"

    def test_title_detail_shape(self):
        data = [{"title": "Get passport", "detail": "Must be valid 6 months"}]
        result = assemble_checklist({"id": "prep"}, data, {}, None)
        assert result is not None
        assert result["items"][0]["label"] == "Get passport"
        assert result["items"][0]["note"] == "Must be valid 6 months"

    def test_label_shape(self):
        data = [{"label": "Sunscreen", "note": "SPF 50+"}]
        result = assemble_checklist({"id": "pack"}, data, {}, None)
        assert result is not None
        assert result["items"][0]["label"] == "Sunscreen"

    def test_plain_strings(self):
        data = ["item1", "item2"]
        result = assemble_checklist({"id": "list"}, data, {}, None)
        assert result is not None
        assert len(result["items"]) == 2
        assert result["items"][0]["label"] == "item1"

    def test_should_drop_dict_without_text_field_instead_of_printing_its_repr(self):
        day = {"day": 1, "city": "Lisbon", "spots": [{"name": "Torre de Belém"}]}
        result = assemble_checklist({"id": "tips"}, [day, "Buy a Viva Viagem card"], {}, None)
        assert result == {"items": [{"label": "Buy a Viva Viagem card"}]}

    def test_should_return_none_when_no_item_has_a_label(self):
        days = [{"day": 1, "city": "Lisbon"}, {"day": 2, "city": "Lisbon"}]
        assert assemble_checklist({"id": "tips"}, days, {}, None) is None


class TestFlexComparison:
    """Tests for cross-domain comparison input shapes."""

    def test_name_description_shape_routes_elsewhere(self):
        # {name, description} is single-sided — belongs in info_grid or
        # spot_explorer, never in comparison. The previous coercion produced
        # the audited "column 3 empty" rendering on review videos.
        data = [
            {"name": "React", "emoji": "⚛️", "description": "Component-based UI library"},
            {"name": "Vue", "emoji": "💚", "description": "Progressive framework"},
        ]
        assert assemble_comparison({}, data, {}, None) is None

    def test_cheatsheet_routes_to_code_explorer_not_comparison(self):
        # {title, description, code} is code-snippet-shaped — route via
        # code_explorer. Comparison no longer coerces this shape.
        data = [
            {
                "title": "useState",
                "description": "State hook",
                "code": "const [x, setX] = useState()",
            }
        ]
        assert assemble_comparison({}, data, {}, None) is None

    def test_empty_returns_none(self):
        assert assemble_comparison({}, [], {}, None) is None


class TestFlexInfoGrid:
    """Tests for cross-domain info_grid input shapes."""

    def test_flat_dict(self):
        data = {"Display": "6.7 inch", "Battery": "5000mAh", "Price": 999}
        result = assemble_info_grid({}, data, {}, None)
        assert result is not None
        assert len(result["items"]) == 3
        keys = [p["key"] for p in result["items"]]
        assert "Display" in keys

    def test_name_value_shape(self):
        data = [{"name": "Director", "value": "Christopher Nolan"}]
        result = assemble_info_grid({}, data, {}, None)
        assert result is not None
        assert result["items"][0]["key"] == "Director"

    def test_label_description_shape(self):
        data = [{"label": "Genre", "description": "Sci-Fi"}]
        result = assemble_info_grid({}, data, {}, None)
        assert result is not None
        assert result["items"][0]["key"] == "Genre"
        assert result["items"][0]["value"] == "Sci-Fi"

    def test_role_name_shape(self):
        data = [{"role": "Vocals", "name": "Freddie Mercury"}]
        result = assemble_info_grid({}, data, {}, None)
        assert result is not None
        assert result["items"][0]["key"] == "Vocals"
        assert result["items"][0]["value"] == "Freddie Mercury"

    def test_title_description_shape(self):
        data = [{"title": "Check for source maps", "description": "Verify no .map files"}]
        result = assemble_info_grid({}, data, {}, None)
        assert result is not None
        assert result["items"][0]["key"] == "Check for source maps"
        assert result["items"][0]["value"] == "Verify no .map files"

    def test_title_value_shape(self):
        data = [{"title": "Display", "value": "6.7-inch AMOLED"}]
        result = assemble_info_grid({}, data, {}, None)
        assert result is not None
        assert result["items"][0]["key"] == "Display"
        assert result["items"][0]["value"] == "6.7-inch AMOLED"

    def test_title_code_description_shape(self):
        """Items with {title, code, description} should normalize to {key=title, value=description}."""
        data = [
            {
                "title": "Enable extra usage",
                "code": "dashboard → settings",
                "description": "Allows filtered requests",
            },
            {
                "title": "Switch to Codex",
                "code": "alias cc='codex'",
                "description": "Replace Claude alias",
            },
        ]
        result = assemble_info_grid({}, data, {}, None)
        assert result is not None
        assert len(result["items"]) == 2
        assert result["items"][0]["key"] == "Enable extra usage"
        assert result["items"][0]["value"] == "Allows filtered requests"
        assert result["items"][1]["key"] == "Switch to Codex"

    def test_name_explanation_shape(self):
        """Items with {name, explanation} should normalize to {key=name, value=explanation}."""
        data = [
            {
                "name": "Past perfect tense",
                "emoji": "⏰",
                "explanation": "Use had + past participle",
            }
        ]
        result = assemble_info_grid({}, data, {}, None)
        assert result is not None
        assert result["items"][0]["key"] == "Past perfect tense"
        assert result["items"][0]["value"] == "Use had + past participle"

    # ─── Audited-bug regression tests ──────────────────────────────
    # The following shapes were ALL silently producing empty-card grids on
    # real videos (Stocks "8 Key Signals", Transformers "14 Core Concepts",
    # Karpathy "14 Core Concepts", Travel "60 Insider Tips"). Each shape now
    # has explicit normalizer coverage.

    def test_name_definition_shape_concepts(self):
        # Most common concept shape from the LLM. Was the root cause of half
        # the audited empty-grid renderings.
        data = [
            {"name": "MCP", "definition": "Model Context Protocol"},
            {"name": "Attention", "definition": "Weighted sum over token embeddings"},
        ]
        result = assemble_info_grid({}, data, {}, None)
        assert result is not None
        assert result["items"][0]["key"] == "MCP"
        assert result["items"][0]["value"] == "Model Context Protocol"

    def test_name_definition_example_shape(self):
        # Example field surfaces as `evidence` so the card has supporting
        # context without crowding the primary value text.
        data = [
            {
                "name": "Tokenization",
                "definition": "Splitting text into units",
                "example": "'Hello world' → ['Hello', 'world']",
            }
        ]
        result = assemble_info_grid({}, data, {}, None)
        assert result is not None
        assert result["items"][0]["value"] == "Splitting text into units"
        assert result["items"][0]["evidence"] == "'Hello world' → ['Hello', 'world']"

    def test_title_detail_source_science_keyfacts(self):
        # science.keyFacts shape — title→key, detail→value, source→evidence.
        data = [
            {
                "emoji": "🔬",
                "title": "Heat capacity",
                "detail": "Water has higher heat capacity than air",
                "source": "Physics 101 chapter 4",
            }
        ]
        result = assemble_info_grid({}, data, {}, None)
        assert result is not None
        assert result["items"][0]["key"] == "Heat capacity"
        assert result["items"][0]["evidence"] == "Physics 101 chapter 4"

    def test_aspect_detail_music_analysis(self):
        # music.analysis shape — aspect→key, detail→value.
        data = [{"aspect": "Tempo", "detail": "Steady 120 BPM", "emoji": "🎵"}]
        result = assemble_info_grid({}, data, {}, None)
        assert result is not None
        assert result["items"][0]["key"] == "Tempo"
        assert result["items"][0]["value"] == "Steady 120 BPM"

    def test_phrase_translation_context_language(self):
        # language.phrases shape — phrase→key, translation→value, context→evidence.
        data = [
            {
                "phrase": "Quel dommage",
                "translation": "What a shame",
                "context": "Said when something unfortunate happens",
            }
        ]
        result = assemble_info_grid({}, data, {}, None)
        assert result is not None
        assert result["items"][0]["key"] == "Quel dommage"
        assert result["items"][0]["value"] == "What a shame"
        assert result["items"][0]["evidence"].startswith("Said when")

    def test_term_definition_shape(self):
        data = [{"term": "ReLU", "definition": "Rectified Linear Unit — max(0, x)"}]
        result = assemble_info_grid({}, data, {}, None)
        assert result is not None
        assert result["items"][0]["key"] == "ReLU"

    def test_fact_explanation_shape(self):
        data = [
            {"fact": "DNA stores genetic information", "explanation": "Via base-pair sequences"}
        ]
        result = assemble_info_grid({}, data, {}, None)
        assert result is not None
        assert result["items"][0]["key"] == "DNA stores genetic information"

    def test_keeps_key_only_items_as_headline_chips(self):
        # Key-only items survive as headline chips — the frontend explicitly
        # renders them (terms-only glossaries); only keyless items drop.
        data = [
            {"name": "Real concept", "definition": "Has substance"},
            {"name": "Headline only"},  # ← kept as key-only chip
            {"name": "", "definition": "no key"},  # ← dropped (no key)
        ]
        result = assemble_info_grid({}, data, {}, None)
        assert result is not None
        assert len(result["items"]) == 2
        assert result["items"][0]["key"] == "Real concept"
        assert result["items"][1] == {"key": "Headline only", "value": ""}

    def test_returns_none_when_nothing_normalizes(self):
        # Every item fails normalization → no items → drop the tab so the
        # fallback layer can route the data elsewhere.
        data = [{"only": "garbage"}, {"more": "garbage"}]
        assert assemble_info_grid({}, data, {}, None) is None

    def test_key_value_shape_keeps_key_only_drops_keyless(self):
        # {key, value} items with an empty value survive as headline chips
        # (the frontend renders them); keyless items still drop. Symmetric
        # with the fallback path's key-only policy.
        data = [
            {"key": "Real", "value": "Has body"},
            {"key": "Empty value", "value": ""},  # ← kept as key-only chip
            {"key": "Whitespace", "value": "   "},  # ← kept as key-only chip
            {"key": "", "value": "no key"},  # ← dropped (no key)
        ]
        result = assemble_info_grid({}, data, {}, None)
        assert result is not None
        assert len(result["items"]) == 3
        assert [i["value"] for i in result["items"]] == ["Has body", "", ""]
        assert result["items"][0]["key"] == "Real"

    def test_drops_comparison_single_sided_rows_per_row(self):
        # Regression: previously a comparison with any one real-pair row kept
        # ALL rows including single-sided ones, which rendered as half-empty
        # rows next to the real pairs. Now single-sided rows are dropped
        # individually so the table only contains usable comparisons.
        from src.services.pipeline.assembly.assemblers_primary import assemble_comparison

        data = [
            {"feature": "Battery", "thisProduct": "12h", "competitor": "8h"},
            {"feature": "Camera", "thisProduct": "12MP", "competitor": ""},  # ← drop
            {"feature": "Weight", "thisProduct": "", "competitor": "1.6kg"},  # ← drop
            {"feature": "Price", "thisProduct": "$999", "competitor": "$1099"},
        ]
        result = assemble_comparison(
            {},
            data,
            {"review": {"product": "Phone A"}},
            None,
        )
        assert result is not None
        # Only the two genuine pair rows survive.
        assert len(result["comparisons"]) == 2
        features = [r["feature"] for r in result["comparisons"]]
        assert features == ["Battery", "Price"]


class TestSpotExplorerEmptyFiltering:
    """Tests for filtering empty/low-quality spots in spot_explorer assembler."""

    def test_empty_spots_are_dropped(self):
        data = [
            {"name": "", "description": ""},
            {"name": "Valid Spot", "description": "Has content"},
            {"name": "", "description": ""},
            {"name": "Second Valid", "description": "Also real content"},
        ]
        result = assemble_spot_explorer({}, data, {}, None)
        assert result is not None
        assert len(result["spots"]) == 2
        assert result["spots"][0]["name"] == "Valid Spot"
        assert result["spots"][1]["name"] == "Second Valid"

    def test_all_empty_spots_returns_none(self):
        data = [{"name": "", "description": ""}, {"name": "", "description": ""}]
        result = assemble_spot_explorer({}, data, {}, None)
        assert result is None

    def test_spot_with_only_name_is_dropped(self):
        """A spot with just a name (no description or passthrough) renders as an empty card — drop it."""
        data = [
            {"name": "Has name only", "description": ""},
            {"name": "Also name only", "description": ""},
        ]
        result = assemble_spot_explorer({}, data, {}, None)
        assert result is None

    def test_spot_with_only_description_is_dropped(self):
        """A spot with no name cannot be the title of a card — drop it."""
        data = [
            {"name": "", "description": "Has description only"},
            {"name": "", "description": "Another orphan description"},
        ]
        result = assemble_spot_explorer({}, data, {}, None)
        assert result is None

    def test_bare_string_list_rejected(self):
        """List of raw strings (keyword pollution) should not assemble as spots."""
        data = [
            "live dashboards",
            "API integration",
            "data visualization",
            "scheduled data refresh",
        ]
        result = assemble_spot_explorer({}, data, {}, None)
        assert result is None

    def test_below_minimum_spot_count_returns_none(self):
        """A single valid spot is not an explorer — drop so fallback layer can recover."""
        data = [{"name": "Lonely Spot", "description": "Only one"}]
        result = assemble_spot_explorer({}, data, {}, None)
        assert result is None

    def test_name_plus_emoji_is_kept(self):
        """Name + emoji-only (no description) is still meaningful visual content."""
        data = [
            {"name": "Tokyo Tower", "emoji": "🗼"},
            {"name": "Sky Tree", "emoji": "🗾"},
        ]
        result = assemble_spot_explorer({}, data, {}, None)
        assert result is not None
        assert len(result["spots"]) == 2
        assert result["spots"][0]["emoji"] == "🗼"

    def test_mixed_valid_and_invalid_keeps_only_valid(self):
        data = [
            {"name": "Real Place", "description": "With detail"},
            {"name": "Name only"},
            {"name": "Another Real Place", "emoji": "📍"},
            {"name": ""},
            {"description": "Orphan description"},
        ]
        result = assemble_spot_explorer({}, data, {}, None)
        assert result is not None
        assert len(result["spots"]) == 2
        assert result["spots"][0]["name"] == "Real Place"
        assert result["spots"][1]["name"] == "Another Real Place"

    def test_travel_day_empty_spots_drops_tab(self):
        """TravelDay where every day's spots are name-only — whole tab drops."""
        data = [
            {"day": 1, "city": "Rome", "spots": [{"name": "Colosseum"}, {"name": "Forum"}]},
            {"day": 2, "city": "Florence", "spots": [{"name": "Duomo"}]},
        ]
        result = assemble_spot_explorer({}, data, {}, None)
        assert result is None

    def test_travel_day_section_indices_stay_aligned_after_filter(self):
        """When some day-spots are filtered, sections' spotIndices must re-align to the kept list."""
        data = [
            {
                "day": 1,
                "city": "Rome",
                "spots": [
                    {"name": "Colosseum", "description": "Ancient amphitheatre"},
                    {"name": "Name only"},
                    {"name": "Forum", "description": "Political heart"},
                ],
            },
            {
                "day": 2,
                "city": "Florence",
                "spots": [
                    {"name": "Duomo"},
                ],
            },
            {
                "day": 3,
                "city": "Venice",
                "spots": [
                    {"name": "St Mark's", "description": "Basilica square"},
                ],
            },
        ]
        result = assemble_spot_explorer({}, data, {}, None)
        assert result is not None
        assert len(result["spots"]) == 3
        assert result["spots"][0]["name"] == "Colosseum"
        assert result["spots"][1]["name"] == "Forum"
        assert result["spots"][2]["name"] == "St Mark's"
        assert len(result["sections"]) == 2
        assert result["sections"][0]["label"] == "Day 1: Rome"
        assert result["sections"][0]["spotIndices"] == [0, 1]
        assert result["sections"][1]["label"] == "Day 3: Venice"
        assert result["sections"][1]["spotIndices"] == [2]


class TestComparisonProductLabel:
    """Tests for product name extraction in comparison assembler."""

    def test_product_from_review_extraction(self):
        data = {"pros": ["Fast"], "cons": [], "comparisons": []}
        extraction = {"review": {"product": "Pixel 8 Pro", "pros": ["Fast"]}}
        result = assemble_comparison({}, data, extraction, None)
        assert result is not None
        assert result["leftLabel"] == "Pixel 8 Pro"

    def test_no_video_title_fallback(self):
        # Video title MUST NOT be used as leftLabel — that produced the audited
        # "I'm Doubling Down On 2 Stocks Before June" column header on the
        # stocks Risk Check tab. Only review.product is allowed as the label.
        data = {"pros": ["Great"], "cons": [], "comparisons": []}
        tab = {"_video_meta": {"title": "Claude Code is unusable now"}}
        result = assemble_comparison(tab, data, {}, None)
        assert result is not None
        assert result["leftLabel"] == ""

    def test_empty_label_when_no_product(self):
        data = {"pros": ["Good"], "cons": [], "comparisons": []}
        result = assemble_comparison({}, data, {}, None)
        assert result is not None
        assert result["leftLabel"] == ""
        assert result["rightLabel"] == ""

    def test_review_product_used_when_explicit(self):
        data = {"pros": ["Fast"], "cons": [], "comparisons": []}
        tab = {"_video_meta": {"title": "Is Pixel 8 Worth It?"}}
        extraction = {"review": {"product": "Google Pixel 8 Pro"}}
        result = assemble_comparison(tab, data, extraction, None)
        assert result is not None
        assert result["leftLabel"] == "Google Pixel 8 Pro"

    def test_list_with_no_real_pairs_returns_none(self):
        # Single-side rows used to coerce into comparison rows via the title
        # → feature, description → thisProduct, code → competitor pathway.
        # The new contract requires actual A-vs-B pairs.
        data = [
            {"title": "Battery", "description": "5000mAh", "code": "All day"},
        ]
        tab = {"_video_meta": {"title": "Phone Review"}}
        assert assemble_comparison(tab, data, {}, None) is None


# ─── Data Quality: Normalizer Tests ───


class TestCodeExplorerNormalization:
    """Phase 1.1: _normalize_code_snippet + assemble_code_explorer."""

    def test_string_item_becomes_snippet(self):
        result = _normalize_code_snippet("console.log('hello')")
        assert result is not None
        assert result["code"] == "console.log('hello')"
        assert result["language"] == "text"

    def test_empty_string_returns_none(self):
        assert _normalize_code_snippet("") is None
        assert _normalize_code_snippet("  ") is None

    def test_dict_with_code_key(self):
        result = _normalize_code_snippet({"code": "x = 1", "language": "python"})
        assert result is not None
        assert result["code"] == "x = 1"
        assert result["language"] == "python"

    def test_dict_alias_snippet(self):
        result = _normalize_code_snippet({"snippet": "fn main() {}", "lang": "rust"})
        assert result is not None
        assert result["code"] == "fn main() {}"
        assert result["language"] == "rust"

    def test_dict_missing_code_returns_none(self):
        assert _normalize_code_snippet({"language": "python"}) is None

    def test_non_dict_non_str_returns_none(self):
        assert _normalize_code_snippet(42) is None
        assert _normalize_code_snippet(None) is None

    def test_assembler_normalizes_mixed_items(self):
        data = [
            "const x = 1",
            {"code": "fn main() {}", "language": "rust"},
            42,  # dropped
            {"language": "go"},  # dropped — no code
        ]
        result = assemble_code_explorer({}, data, {}, None)
        assert result is not None
        assert len(result["snippets"]) == 2

    def test_assembler_all_invalid_returns_none(self):
        assert assemble_code_explorer({}, [42, None, ""], {}, None) is None

    def test_valid_data_unchanged(self):
        """Existing valid data passes through without alteration."""
        result = _normalize_code_snippet(
            {
                "code": "print(1)",
                "language": "python",
                "explanation": "Prints 1",
                "filename": "main.py",
            }
        )
        assert result["code"] == "print(1)"
        assert result["explanation"] == "Prints 1"
        assert result["filename"] == "main.py"


class TestMomentItemNormalization:
    """_normalize_moment_item + assemble_moment_track — merged point/clip normalization."""

    def test_valid_entry_passthrough(self):
        result = _normalize_moment_item({"label": "Intro", "time": "0:00", "seconds": 0}, 0)
        assert result is not None
        assert result["label"] == "Intro"
        assert result["seconds"] == 0
        # No endSeconds ⇒ point moment
        assert "endSeconds" not in result

    def test_missing_time_computed(self):
        result = _normalize_moment_item({"label": "Test", "seconds": 125}, 0)
        assert result is not None
        assert result["time"] == "2:05"

    def test_label_aliases(self):
        result = _normalize_moment_item({"title": "Chapter 1", "seconds": 0}, 0)
        assert result["label"] == "Chapter 1"
        result = _normalize_moment_item({"name": "Section A", "seconds": 0}, 0)
        assert result["label"] == "Section A"

    def test_string_entry(self):
        result = _normalize_moment_item("Introduction", 0)
        assert result is not None
        assert result["label"] == "Introduction"
        assert result["seconds"] == 0

    def test_empty_string_returns_none(self):
        assert _normalize_moment_item("", 0) is None

    def test_non_dict_non_str_returns_none(self):
        assert _normalize_moment_item(42, 0) is None

    def test_missing_label_gets_default(self):
        result = _normalize_moment_item({"seconds": 60}, 2)
        assert result["label"] == "Moment 3"

    def test_label_from_description_when_no_title(self):
        """When label/title/name are absent, derive from description prefix."""
        result = _normalize_moment_item(
            {"timestamp": 30, "description": "Amazing goal sailed over the defender"}, 0
        )
        assert result["label"] == "Amazing goal sailed over the defender"[:60]

    def test_endSeconds_preserved_when_valid(self):
        """endSeconds is kept when > seconds + 1 (real span)."""
        result = _normalize_moment_item({"label": "Demo", "seconds": 100, "endSeconds": 160}, 0)
        assert result["endSeconds"] == 160

    def test_endSeconds_dropped_when_too_short(self):
        """endSeconds is dropped when it's ≤ seconds + 1 (not a real span)."""
        result = _normalize_moment_item({"label": "X", "seconds": 100, "endSeconds": 101}, 0)
        assert "endSeconds" not in result

    def test_startSeconds_maps_to_seconds(self):
        """Legacy clip shape using startSeconds maps into canonical seconds."""
        result = _normalize_moment_item({"label": "X", "startSeconds": 42}, 0)
        assert result["seconds"] == 42

    def test_endTimestamp_alias(self):
        """narrative.keyMoments uses endTimestamp — the normalizer accepts it."""
        result = _normalize_moment_item({"label": "X", "timestamp": 200, "endTimestamp": 260}, 0)
        assert result["seconds"] == 200
        assert result["endSeconds"] == 260

    def test_optional_fields_passthrough(self):
        result = _normalize_moment_item(
            {
                "label": "X",
                "seconds": 10,
                "description": "detail",
                "mood": "excited",
                "emoji": "🔥",
                "speaker": "Gordon",
                "tags": ["highlight"],
                "thumbnailUrl": "https://a/b.jpg",
            },
            0,
        )
        assert result["description"] == "detail"
        assert result["mood"] == "excited"
        assert result["emoji"] == "🔥"
        assert result["speaker"] == "Gordon"
        assert result["tags"] == ["highlight"]
        assert result["thumbnailUrl"] == "https://a/b.jpg"

    def test_assembler_normalizes(self):
        data = [
            {"title": "Start", "timestamp": 0},
            {"label": "Middle", "seconds": 120},
            42,  # dropped
        ]
        result = assemble_moment_track({}, data, {}, None)
        assert result is not None
        assert len(result["items"]) == 2
        assert result["items"][0]["label"] == "Start"
        assert result["items"][1]["time"] == "2:00"

    def test_mixed_points_and_spans(self):
        """Single items[] with both navigation points and highlight spans."""
        data = [
            {"label": "Intro", "timestamp": 0},  # point
            {"label": "Punchline", "timestamp": 60, "endTimestamp": 78},  # span
            {"label": "Wrap", "timestamp": 200},  # point
        ]
        result = assemble_moment_track({}, data, {}, None)
        items = result["items"]
        assert len(items) == 3
        assert "endSeconds" not in items[0]
        assert items[1]["endSeconds"] == 78
        assert "endSeconds" not in items[2]

    # ─── Malformed-input safety net (P1 sign-off) ────────────────────────
    # These guard the chain `label or title or name or description[:60] or
    # "Moment N+1"` against JSON `null`, empty strings, and string-typed
    # numerics that LLMs occasionally emit despite the schema saying int.

    def test_null_label_falls_back_to_description(self):
        """JSON null label should not block the description fallback."""
        result = _normalize_moment_item(
            {"label": None, "seconds": 30, "description": "Demo of pivot table"},
            0,
        )
        assert result is not None
        assert result["label"] == "Demo of pivot table"

    def test_null_label_and_title_fall_back_to_name(self):
        """Cascading nulls don't short-circuit the alias chain."""
        result = _normalize_moment_item(
            {"label": None, "title": None, "name": "Section A", "seconds": 0},
            0,
        )
        assert result["label"] == "Section A"

    def test_seconds_as_numeric_string_coerces_to_int(self):
        """LLM sometimes returns `"seconds": "125"` despite schema asking for int."""
        result = _normalize_moment_item({"label": "X", "seconds": "125"}, 0)
        assert result is not None and result["seconds"] == 125

    def test_seconds_as_numeric_string_renders_time(self):
        """The coerced int feeds the time formatter — verify the visible label."""
        result = _normalize_moment_item({"label": "X", "seconds": "125"}, 0)
        assert result is not None and result["time"] == "2:05"

    def test_seconds_as_garbage_string_falls_back_to_zero(self):
        """Unparseable seconds shouldn't crash the assembler — fall back to 0."""
        result = _normalize_moment_item({"label": "X", "seconds": "abc"}, 0)
        assert result is not None and result["seconds"] == 0

    def test_seconds_as_garbage_string_renders_zero_time(self):
        """The 0-second fallback formats as 0:00, not blank or 'None'."""
        result = _normalize_moment_item({"label": "X", "seconds": "abc"}, 0)
        assert result is not None and result["time"] == "0:00"

    def test_null_endSeconds_omitted(self):
        """JSON null endSeconds collapses to a point moment (no endSeconds key)."""
        result = _normalize_moment_item(
            {"label": "X", "seconds": 100, "endSeconds": None},
            0,
        )
        assert result is not None
        assert "endSeconds" not in result


class TestMomentTrackClipDefaults:
    """assemble_moment_track with clip-style input — default value behavior."""

    def test_label_fallback_chain(self):
        result = assemble_moment_track({}, [{"timestamp": 10}], {}, None)
        assert result is not None
        # No label/title/description ⇒ default "Moment 1"
        assert result["items"][0]["label"] == "Moment 1"

    def test_label_from_title(self):
        result = assemble_moment_track({}, [{"title": "Great moment"}], {}, None)
        assert result["items"][0]["label"] == "Great moment"

    def test_seconds_from_timestamp(self):
        result = assemble_moment_track({}, [{"label": "X", "timestamp": 120}], {}, None)
        assert result["items"][0]["seconds"] == 120
        assert result["items"][0]["time"] == "2:00"
        # No endSeconds ⇒ point, not span
        assert "endSeconds" not in result["items"][0]

    def test_optional_fields_omitted_when_absent(self):
        """Optional fields (description, mood, ...) are dropped — not defaulted."""
        result = assemble_moment_track({}, [{"label": "X"}], {}, None)
        item = result["items"][0]
        assert "description" not in item
        assert "mood" not in item


class TestComparisonNullHandling:
    """Phase 1.7: JSON null → Python None for pros/cons."""

    def test_null_pros_cons_handled(self):
        """JSON null becomes Python None — `or []` fixes it."""
        data = {
            "pros": None,
            "cons": None,
            "comparisons": [
                {"feature": "Speed", "thisProduct": "Fast", "competitor": "Slow", "winner": "left"},
            ],
        }
        result = assemble_comparison({}, data, {}, None)
        assert result is not None
        assert isinstance(result["pros"], list)
        assert isinstance(result["cons"], list)
        # Winner synthesis still works
        assert "Speed" in result["pros"]

    def test_null_comparisons_handled(self):
        data = {"pros": ["Good"], "cons": ["Bad"], "comparisons": None}
        result = assemble_comparison({}, data, {}, None)
        assert result is not None
        assert result["pros"] == ["Good"]
        assert result["cons"] == ["Bad"]
