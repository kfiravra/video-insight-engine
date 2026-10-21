"""Assembler unit tests — learning/media family.

exercise, quiz, flash_deck, scenario, verdict, overview, gallery, lyrics.
"""

import pytest

from src.models.domain_types import validate_domain_output
from src.services.pipeline.assembly import (
    _normalize_exercise,
    _normalize_quiz_question,
    _normalize_scenario_item,
    assemble_exercise_tracker,
    assemble_flash_deck,
    assemble_gallery,
    assemble_lyrics_player,
    assemble_overview,
    assemble_quiz,
    assemble_scenario,
    assemble_verdict,
)

# ─── resolve_data_source ───


class TestAssembleExerciseTracker:
    def test_dict_with_exercises(self):
        data = {
            "exercises": [{"name": "Push-up"}],
            "warmup": [{"name": "Arm circles"}],
            "cooldown": [],
        }
        result = assemble_exercise_tracker({}, data, {}, None)
        assert result is not None
        assert len(result["exercises"]) == 1
        assert len(result["warmup"]) == 1
        assert "cooldown" not in result  # Empty cooldown omitted

    def test_list_of_exercises(self):
        data = [{"name": "Squat"}, {"name": "Lunge"}]
        result = assemble_exercise_tracker({}, data, {}, None)
        assert result is not None
        assert len(result["exercises"]) == 2

    def test_empty(self):
        assert assemble_exercise_tracker({}, {"exercises": [], "warmup": []}, {}, None) is None


class TestAssembleFlashDeck:
    def test_concept_normalization(self):
        data = [
            {"name": "REST", "definition": "Representational State Transfer", "emoji": "🌐"},
            {"front": "GraphQL", "back": "Query language"},
        ]
        result = assemble_flash_deck({}, data, {}, None)
        assert result is not None
        assert result["cards"][0]["front"] == "REST"
        assert result["cards"][0]["back"] == "Representational State Transfer"
        assert result["cards"][1]["front"] == "GraphQL"

    def test_empty(self):
        assert assemble_flash_deck({}, [], {}, None) is None


class TestAssembleVerdict:
    """assemble_verdict was retired as a standalone component; cached
    `assembledTabs` rows still reference component key "verdict" so the
    assembler now emits ComparisonInteractive-shaped props with the verdict
    folded under `verdict`. ReviewSummary in ComparisonInteractive renders it."""

    def test_valid_verdict_returns_comparison_shape(self):
        # The badge renders only with the second the verdict is said and its words
        # (R2, p1.4; D13 extended, output-quality 2.0 c).
        data = {
            "badge": "recommended",
            "bottomLine": "Great product",
            "bestFor": ["students"],
            "notFor": ["pros"],
            "timestamp": 598,
            "quote": "I recommend it to students.",
        }
        result = assemble_verdict({}, data, {}, None)
        assert result is not None
        assert result["comparisons"] == []
        assert result["pros"] == []
        assert result["cons"] == []
        assert result["verdict"]["badge"] == "recommended"
        assert result["verdict"]["bottomLine"] == "Great product"
        assert result["verdict"]["bestFor"] == ["students"]
        assert result["verdict"]["notFor"] == ["pros"]

    def test_includes_score_when_provided(self):
        # 0b.13 / 2.0 c: a score is a fact only when anchored in the video (timestamp)
        # and given in the reviewer's words (quote).
        data = {
            "badge": "best_in_class",
            "bottomLine": "Top pick",
            "score": 8.5,
            "maxScore": 10,
            "timestamp": 600,
            "quote": "Eight and a half out of ten.",
        }
        result = assemble_verdict({}, data, {}, None)
        assert result is not None
        assert result["verdict"]["score"] == 8.5
        assert result["verdict"]["maxScore"] == 10

    def test_should_omit_a_badge_the_reviewer_did_not_state(self):
        result = assemble_verdict({}, {"badge": None, "bottomLine": "Great product"}, {}, None)
        assert "badge" not in result["verdict"]

    def test_empty(self):
        assert assemble_verdict({}, None, {}, None) is None
        assert assemble_verdict({}, [], {}, None) is None


class TestAssembleOverview:
    def test_extracts_primitives(self):
        extraction = {"learning": {"summary": "A great video", "keyQuestion": "What is ML?"}}
        result = assemble_overview({"_primary_tag": "learning"}, None, extraction, None)
        assert result is not None
        assert result["data"]["summary"] == "A great video"

    def test_flattens_meta(self):
        extraction = {"food": {"meta": {"difficulty": "easy", "servings": 4}, "ingredients": []}}
        result = assemble_overview({"_primary_tag": "food"}, None, extraction, None)
        assert result is not None
        assert result["data"]["difficulty"] == "easy"
        assert result["data"]["servings"] == 4

    @pytest.mark.parametrize(
        ("tag", "data"),
        [
            ("fitness", {"exercises": [{"name": "Squat"}]}),
            ("project", {"steps": [{"number": 1, "instruction": "Cut"}]}),
            ("language", {"targetLanguage": "French"}),
            ("science", {"field": "Physics"}),
        ],
    )
    def test_should_show_no_level_name_or_difficulty_the_video_does_not_state(
        self, tag: str, data: dict
    ):
        extraction = validate_domain_output([tag], [], data)
        tab = {"_primary_tag": tag, "_video_meta": {"title": "T"}}

        result = assemble_overview(tab, None, extraction, None)

        assert {"level", "difficulty", "projectName"} & result["data"].keys() == set()

    def test_should_show_the_level_the_video_states(self):
        extraction = validate_domain_output(["fitness"], [], {"meta": {"difficulty": "beginner"}})
        result = assemble_overview({"_primary_tag": "fitness"}, None, extraction, None)
        assert result["data"]["level"] == "Beginner"

    def test_no_domain_data(self):
        assert assemble_overview({"_primary_tag": "travel"}, None, {}, None) is None

    def test_should_not_spread_a_blank_domain_string(self):
        extraction = {"learning": {"summary": "  ", "keyQuestion": "What is ML?"}}
        result = assemble_overview({"_primary_tag": "learning"}, None, extraction, None)
        assert "summary" not in result["data"]


# ─── Registry Coverage ───


class TestAssembleGallery:
    def test_with_images(self):
        data = [
            {"url": "https://example.com/img1.jpg", "caption": "View"},
            {"url": "https://example.com/img2.jpg", "caption": "Beach"},
        ]
        result = assemble_gallery({}, data, {}, None)
        assert result is not None
        assert len(result["images"]) == 2
        assert result["images"][0]["url"] == "https://example.com/img1.jpg"

    def test_empty(self):
        assert assemble_gallery({}, [], {}, None) is None
        assert assemble_gallery({}, None, {}, None) is None


class TestAssembleLyricsPlayer:
    def test_with_structure_and_lyrics(self):
        extraction = {
            "music": {
                "title": "Bohemian Rhapsody",
                "artist": "Queen",
                "structure": [{"name": "Intro", "timestamp": 0}],
                "lyrics": [{"timestamp": 5, "line": "Is this the real life?"}],
            }
        }
        result = assemble_lyrics_player({}, None, extraction, None)
        assert result is not None
        assert result["artist"] == "Queen"
        assert len(result["sections"]) >= 1
        # Frontend contract (lyricsKaraokeSchema) keys lines on "text", not the
        # extraction-side "line" — a "line" key silently degrades every music tab.
        first_line = result["sections"][0]["lines"][0]
        assert first_line["text"] == "Is this the real life?"
        assert "line" not in first_line

    def test_structure_only(self):
        extraction = {
            "music": {
                "title": "Song",
                "artist": "Artist",
                "structure": [{"name": "Verse 1"}],
                "lyrics": [],
            }
        }
        result = assemble_lyrics_player({}, None, extraction, None)
        assert result is not None
        assert "sections" in result

    def test_no_music_returns_none(self):
        assert assemble_lyrics_player({}, None, {}, None) is None
        assert assemble_lyrics_player({}, None, {"music": {}}, None) is None

    def test_no_structure_no_lyrics(self):
        extraction = {"music": {"title": "X", "artist": "Y", "structure": [], "lyrics": []}}
        assert assemble_lyrics_player({}, None, extraction, None) is None


class TestExerciseNormalization:
    """Phase 1.3: _normalize_exercise + assemble_exercise_tracker."""

    def test_valid_exercise(self):
        result = _normalize_exercise({"name": "Push-up", "sets": 3, "reps": 10})
        assert result is not None
        assert result["name"] == "Push-up"
        assert result["sets"] == 3

    def test_defaults_filled(self):
        result = _normalize_exercise({"description": "A cool exercise"})
        assert result["name"] == "Exercise"
        assert result["emoji"] == "💪"
        assert result["formCues"] == []
        assert result["modifications"] == []

    def test_string_formcues_coerced(self):
        result = _normalize_exercise({"name": "Squat", "formCues": "Keep back straight"})
        assert result["formCues"] == ["Keep back straight"]

    def test_string_modifications_coerced(self):
        result = _normalize_exercise({"name": "Squat", "modifications": "Use chair"})
        assert result["modifications"] == ["Use chair"]

    def test_non_dict_returns_none(self):
        assert _normalize_exercise("not a dict") is None
        assert _normalize_exercise(42) is None

    def test_assembler_normalizes_list(self):
        data = [
            {"name": "Push-up"},
            {"title": "Squat", "sets": 3},
            "invalid",  # dropped
        ]
        result = assemble_exercise_tracker({}, data, {}, None)
        assert result is not None
        assert len(result["exercises"]) == 2
        assert result["exercises"][0]["emoji"] == "💪"
        assert result["exercises"][1]["name"] == "Squat"

    def test_assembler_normalizes_dict_shape(self):
        data = {"exercises": [{"name": "Lunge"}], "warmup": [{"name": "Jog"}]}
        result = assemble_exercise_tracker({}, data, {}, None)
        assert result is not None
        assert result["exercises"][0]["emoji"] == "💪"


class TestQuizNormalization:
    """Phase 1.4: _normalize_quiz_question + assemble_quiz."""

    def test_valid_question(self):
        result = _normalize_quiz_question(
            {
                "question": "What is 1+1?",
                "options": ["1", "2", "3"],
                "correctIndex": 1,
                "explanation": "Math",
            }
        )
        assert result is not None
        assert result["question"] == "What is 1+1?"
        assert result["correctIndex"] == 1

    def test_text_alias(self):
        result = _normalize_quiz_question({"text": "Q?", "choices": ["A", "B"], "correctIndex": 0})
        assert result is not None
        assert result["question"] == "Q?"

    def test_too_few_options_dropped(self):
        assert _normalize_quiz_question({"question": "Q?", "options": ["A"]}) is None

    def test_missing_options_dropped(self):
        assert _normalize_quiz_question({"question": "Q?"}) is None

    def test_correctindex_clamped(self):
        result = _normalize_quiz_question(
            {"question": "Q?", "options": ["A", "B"], "correctIndex": 99}
        )
        assert result["correctIndex"] == 1

    def test_correctindex_negative_clamped(self):
        result = _normalize_quiz_question(
            {"question": "Q?", "options": ["A", "B"], "correctIndex": -5}
        )
        assert result["correctIndex"] == 0

    def test_empty_question_dropped(self):
        assert _normalize_quiz_question({"question": "", "options": ["A", "B"]}) is None

    def test_non_dict_returns_none(self):
        assert _normalize_quiz_question("not a dict") is None

    def test_assembler_filters_invalid(self):
        data = [
            {"question": "Good?", "options": ["A", "B"], "correctIndex": 0},
            {"question": "", "options": ["A", "B"]},  # dropped
            {"question": "Lonely?", "options": ["A"]},  # dropped
        ]
        result = assemble_quiz({}, data, {}, None)
        assert result is not None
        assert len(result["questions"]) == 1


class TestScenarioNormalization:
    """Phase 1.5: _normalize_scenario_item + assemble_scenario."""

    def test_valid_scenario(self):
        result = _normalize_scenario_item(
            {
                "question": "What do you do?",
                "options": [
                    {"text": "Run", "correct": True, "explanation": "Yes"},
                    {"text": "Hide", "correct": False, "explanation": "No"},
                ],
            }
        )
        assert result is not None
        assert result["question"] == "What do you do?"
        assert len(result["options"]) == 2

    def test_string_options_normalized(self):
        result = _normalize_scenario_item(
            {
                "question": "Pick one",
                "options": ["Option A", "Option B"],
            }
        )
        assert result is not None
        assert result["options"][0] == {"text": "Option A", "correct": False, "explanation": ""}

    def test_situation_alias(self):
        result = _normalize_scenario_item(
            {
                "situation": "Fire alarm rings",
                "options": ["Evacuate", "Ignore"],
            }
        )
        assert result is not None
        assert result["question"] == "Fire alarm rings"

    def test_too_few_options_dropped(self):
        assert _normalize_scenario_item({"question": "Q?", "options": ["A"]}) is None

    def test_empty_question_dropped(self):
        assert _normalize_scenario_item({"question": "", "options": ["A", "B"]}) is None

    def test_non_dict_returns_none(self):
        assert _normalize_scenario_item("not a dict") is None

    def test_assembler_filters_invalid(self):
        data = [
            {"question": "Good?", "options": ["A", "B"]},
            {"question": "", "options": ["A", "B"]},  # dropped
        ]
        result = assemble_scenario({}, data, {}, None)
        assert result is not None
        assert len(result["scenarios"]) == 1


# ─── Flashcard prefix cleanup ───


class TestFlashCardNoEnglishPrefixes:
    """`_to_flash_card` historically prepended English category labels
    ("Form: ", "Duration: ", "Level: ", "Modifications: ", "Example: ")
    when building cards from exercise/vocab/tip data. Those prefixes
    leak English into non-English flashcards. The new contract emits
    only the raw value — the card's `front` already carries the term."""

    def test_exercise_card_has_no_form_or_duration_prefix(self):
        from src.services.pipeline.assembly.normalizers import _to_flash_card

        card = _to_flash_card(
            {
                "name": "Plank",
                "formCues": ["Keep your hips level", "Engage your core"],
                "duration": "30 seconds",
                "difficulty": "intermediate",
                "timestamp": 95,  # 0b.13: the duration ships only on an anchored item
            }
        )
        assert card["front"] == "Plank"
        back = card["back"]
        assert "Form:" not in back
        assert "Duration:" not in back
        assert "Level:" not in back
        # The actual content survives, separated cleanly.
        assert "Keep your hips level" in back
        assert "30 seconds" in back
        assert "intermediate" in back

    def test_exercise_card_with_modifications_drops_prefix(self):
        from src.services.pipeline.assembly.normalizers import _to_flash_card

        card = _to_flash_card(
            {
                "name": "Push-up",
                "formCues": ["Straight back"],
                "modifications": ["Knee push-up", "Wall push-up"],
            }
        )
        assert "Modifications:" not in card["back"]
        assert "Knee push-up" in card["back"]

    def test_vocab_card_drops_example_prefix(self):
        from src.services.pipeline.assembly.normalizers import _to_flash_card

        card = _to_flash_card(
            {
                "word": "Ephemeral",
                "definition": "Lasting for a very short time",
                "example": "Cherry blossoms are ephemeral",
            }
        )
        assert card["front"] == "Ephemeral"
        assert "Example:" not in card["back"]
        assert "Lasting for a very short time" in card["back"]
        assert "Cherry blossoms are ephemeral" in card["back"]

    def test_preserves_pre_built_front_back(self):
        from src.services.pipeline.assembly.normalizers import _to_flash_card

        # Standard front+back input passes through untouched.
        card = _to_flash_card({"front": "Q", "back": "A", "emoji": "💡"})
        assert card == {"front": "Q", "back": "A", "emoji": "💡"}
