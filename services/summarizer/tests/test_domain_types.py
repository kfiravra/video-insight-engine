"""Tests for domain_types Pydantic models."""

import logging

from src.models.domain_types import (
    DOMAIN_MODELS,
    MODIFIER_MODELS,
    VALID_CONTENT_TAGS,
    VALID_MODIFIERS,
    ConceptConnection,
    FinanceCost,
    FinanceData,
    FitnessData,
    FitnessExercise,
    FitnessMeta,
    FitnessTimer,
    FoodData,
    FoodIngredient,
    FoodMeta,
    FoodStep,
    FoodTip,
    LearningConcept,
    LearningData,
    LearningTimestamp,
    MusicCredit,
    MusicData,
    MusicSection,
    NarrativeData,
    ProjectData,
    ReviewComparison,
    ReviewData,
    ReviewRating,
    ReviewVerdict,
    ScienceConcept,
    SectionDefinition,
    TabDefinition,
    TechData,
    TechPattern,
    TravelBudget,
    TravelData,
    TravelDay,
    TravelPackingItem,
    TravelSpot,
    VIEResponse,
    VIEResponseMeta,
    validate_domain_output,
)


class TestTravelData:
    """Test TravelData model."""

    def test_valid_travel_data(self):
        data = TravelData(
            itinerary=[
                TravelDay(
                    day=1,
                    city="Tokyo",
                    spots=[
                        TravelSpot(
                            name="Shibuya Crossing", emoji="🏙️", description="Famous crossing"
                        )
                    ],
                )
            ],
            budget=TravelBudget(total=1500, currency="USD"),
            packingList=[TravelPackingItem(item="Adapter", category="Electronics", essential=True)],
        )
        assert data.itinerary[0].city == "Tokyo"
        assert data.budget.total == 1500

    def test_empty_travel_data(self):
        data = TravelData()
        assert data.itinerary == []
        assert data.budget.total == 0

    def test_budget_coercion(self):
        budget = TravelBudget(total=None, currency=None)
        assert budget.total == 0.0
        assert budget.currency == "USD"

    def test_day_coercion(self):
        day = TravelDay(day=None)
        assert day.day == 1

    def test_alias_support(self):
        data = TravelData.model_validate(
            {
                "packingList": [{"item": "Shoes", "category": "Clothing", "essential": False}],
                "bestSeason": "Spring",
                "accommodationTips": "Stay in Shinjuku",
                "transportationTips": "Get a JR pass",
            }
        )
        assert data.packing_list[0].item == "Shoes"
        assert data.best_season == "Spring"


class TestFoodData:
    """Test FoodData model."""

    def test_valid_food_data(self):
        data = FoodData(
            meta=FoodMeta(prepTime=20, cookTime=30, difficulty="medium"),
            ingredients=[FoodIngredient(name="flour", amount="2", unit="cups")],
            steps=[FoodStep(number=1, instruction="Mix ingredients")],
        )
        assert data.meta.prep_time == 20
        assert len(data.ingredients) == 1

    def test_empty_food_data(self):
        data = FoodData()
        assert data.ingredients == []
        assert data.steps == []

    def test_food_tip_type_coercion(self):
        tip = FoodTip(type="invalid_type", text="Test tip")
        assert tip.type == "chef_tip"

    def test_food_meta_difficulty_coercion(self):
        meta = FoodMeta(difficulty="expert")
        assert meta.difficulty is None

    def test_valid_difficulty_values(self):
        for d in ("easy", "medium", "hard"):
            meta = FoodMeta(difficulty=d)
            assert meta.difficulty == d

    def test_cook_time_string_coercion(self):
        """LLM returns '~12-15 minutes' — should extract first integer."""
        meta = FoodMeta(cookTime="~12-15 minutes")
        assert meta.cook_time == 12

    def test_prep_time_string_coercion(self):
        meta = FoodMeta(prepTime="about 20 min")
        assert meta.prep_time == 20

    def test_cook_time_none_stays_none(self):
        meta = FoodMeta(cookTime=None)
        assert meta.cook_time is None

    def test_cook_time_int_passthrough(self):
        meta = FoodMeta(cookTime=30)
        assert meta.cook_time == 30

    def test_cook_time_float_coercion(self):
        meta = FoodMeta(cookTime=15.5)
        assert meta.cook_time == 15

    def test_cook_time_no_digits_returns_none(self):
        meta = FoodMeta(cookTime="unknown")
        assert meta.cook_time is None

    def test_step_duration_string_coercion(self):
        """FoodStep.duration should also handle string values."""
        step = FoodStep(number=1, instruction="Bake", duration="5-10 minutes")
        assert step.duration == 5

    def test_step_duration_none(self):
        step = FoodStep(number=1, instruction="Mix", duration=None)
        assert step.duration is None

    def test_step_duration_int_passthrough(self):
        step = FoodStep(number=1, instruction="Mix", duration=10)
        assert step.duration == 10


class TestLearningData:
    """Test LearningData model."""

    def test_valid_learning_data(self):
        data = LearningData.model_validate(
            {
                "keyPoints": [{"emoji": "💡", "title": "Point 1", "detail": "Detail"}],
                "concepts": [{"name": "Concept 1", "emoji": "📚", "definition": "A concept"}],
                "takeaways": ["Do this"],
                "timestamps": [{"time": "0:00", "seconds": 0, "label": "Start"}],
                "keyQuestion": "What is this about?",
                "summary": "A summary",
            }
        )
        assert len(data.key_points) == 1
        assert data.key_question == "What is this about?"

    def test_empty_learning_data(self):
        data = LearningData()
        assert data.key_points == []
        assert data.key_question == ""

    def test_timestamp_seconds_coercion(self):
        ts = LearningTimestamp(time="0:00", seconds=None, label="Start")
        assert ts.seconds == 0

    def test_concept_with_connections(self):
        concept = LearningConcept(
            name="OOP",
            definition="Object-oriented programming",
            connections=["Classes", "Inheritance"],
        )
        assert len(concept.connections) == 2

    def test_concept_accepts_typed_connections(self):
        """Typed `{to, type}` connections (current contract) must validate, not
        fall back to passthrough as bare strings would have under `list[str]`."""
        concept = LearningConcept.model_validate(
            {
                "name": "OOP",
                "definition": "Object-oriented programming",
                "connections": [{"to": "Classes", "type": "requires"}],
            }
        )
        assert concept.connections == [ConceptConnection(to="Classes", type="requires")]

    def test_concept_off_enum_relation_does_not_raise(self):
        """An unknown relation must survive validation (assembler owns the enum);
        a strict Literal here would drop the whole domain to passthrough."""
        concept = LearningConcept.model_validate(
            {
                "name": "OOP",
                "definition": "d",
                "connections": [{"to": "Classes", "type": "leadsTo"}],
            }
        )
        assert concept.connections[0].type == "leadsTo"

    def test_concept_mixed_legacy_and_typed_connections(self):
        concept = LearningConcept.model_validate(
            {
                "name": "OOP",
                "definition": "d",
                "connections": ["Classes", {"to": "Inheritance", "type": "partOf"}],
            }
        )
        assert concept.connections[0] == "Classes"
        assert concept.connections[1] == ConceptConnection(to="Inheritance", type="partOf")

    def test_concept_group_is_preserved(self):
        """`group` powers the ConceptCanvas Groups view; it must round-trip
        through `model_dump`, not be silently dropped as an unknown field."""
        concept = LearningConcept.model_validate(
            {
                "name": "OOP",
                "definition": "d",
                "group": "Foundations",
            }
        )
        assert concept.group == "Foundations"
        assert concept.model_dump()["group"] == "Foundations"

    def test_science_concept_accepts_typed_connections_and_group(self):
        concept = ScienceConcept.model_validate(
            {
                "name": "Wave-Particle Duality",
                "definition": "d",
                "group": "Fundamentals",
                "connections": [{"to": "Photon", "type": "relatesTo"}],
            }
        )
        assert concept.group == "Fundamentals"
        assert concept.connections[0].to == "Photon"


class TestReviewData:
    """Test ReviewData model."""

    def test_valid_review_data(self):
        data = ReviewData(
            product="iPhone 15 Pro",
            price="$999",
            rating=ReviewRating(score=8.5, maxScore=10, label="Great"),
            pros=["Great camera"],
            cons=["No USB-C port"],
            verdict=ReviewVerdict(
                badge="recommended",
                bestFor=["Photography"],
                notFor=["Budget buyers"],
                bottomLine="Great phone",
            ),
        )
        assert data.product == "iPhone 15 Pro"
        assert data.rating.score == 8.5

    def test_rating_coercion(self):
        rating = ReviewRating(score=None, maxScore=None, label="")
        assert rating.score == 0.0
        assert rating.max_score == 10.0

    def test_verdict_badge_coercion(self):
        verdict = ReviewVerdict(badge="unknown_badge", bottomLine="Test")
        assert verdict.badge == "recommended"

    def test_comparison_alias(self):
        comp = ReviewComparison.model_validate(
            {
                "feature": "Battery",
                "thisProduct": "5000mAh",
                "competitor": "4500mAh",
                "competitorName": "Pixel 8",
            }
        )
        assert comp.this_product == "5000mAh"
        assert comp.competitor_name == "Pixel 8"


class TestTechData:
    """Test TechData model."""

    def test_valid_tech_data(self):
        data = TechData.model_validate(
            {
                "languages": ["Python", "TypeScript"],
                "frameworks": ["FastAPI"],
                "topics": ["REST API"],
                "setup": {
                    "commands": ["pip install fastapi"],
                    "dependencies": [{"name": "fastapi", "version": "0.100.0"}],
                    "envVars": [{"name": "API_KEY", "description": "Your API key"}],
                },
                "snippets": [{"language": "python", "code": "print('hi')", "explanation": "Hello"}],
                "patterns": [
                    {"title": "DI", "doExample": "good", "dontExample": "bad", "explanation": "Why"}
                ],
                "cheatSheet": [
                    {
                        "title": "Quick start",
                        "code": "uvicorn main:app",
                        "description": "Run server",
                    }
                ],
            }
        )
        assert len(data.languages) == 2
        assert data.setup.commands[0] == "pip install fastapi"

    def test_empty_tech_data(self):
        data = TechData()
        assert data.languages == []
        assert data.snippets == []
        assert data.topics == []

    def test_concepts_to_topics_migration(self):
        """Backward compat: old cached data with 'concepts' should migrate to 'topics'."""
        data = TechData.model_validate(
            {
                "languages": ["Python"],
                "concepts": ["REST API", "GraphQL"],
            }
        )
        assert data.topics == ["REST API", "GraphQL"]

    def test_concepts_not_migrated_when_topics_present(self):
        """If both 'concepts' and 'topics' exist, 'topics' takes precedence."""
        data = TechData.model_validate(
            {
                "topics": ["New Topic"],
                "concepts": ["Old Concept"],
            }
        )
        assert data.topics == ["New Topic"]

    def test_pattern_alias(self):
        pattern = TechPattern.model_validate(
            {
                "title": "Test",
                "doExample": "good code",
                "dontExample": "bad code",
                "explanation": "Because",
            }
        )
        assert pattern.do_example == "good code"


class TestFitnessData:
    """Test FitnessData model."""

    def test_valid_fitness_data(self):
        data = FitnessData(
            meta=FitnessMeta(
                type="HIIT", difficulty="advanced", duration=30, muscleGroups=["legs"]
            ),
            exercises=[FitnessExercise(name="Squats", sets=3, reps="10")],
        )
        assert data.meta.type == "HIIT"
        assert len(data.exercises) == 1

    def test_meta_coercion(self):
        meta = FitnessMeta(type=None, difficulty="expert", duration=None)
        assert meta.type == "general"
        assert meta.difficulty == "intermediate"
        assert meta.duration == 0

    def test_exercise_difficulty_coercion(self):
        exercise = FitnessExercise(name="Push-ups", difficulty="extreme")
        assert exercise.difficulty is None

    def test_timer_rounds_coercion(self):
        timer = FitnessTimer(rounds=None)
        assert timer.rounds == 1


class TestMusicData:
    """Test MusicData model."""

    def test_valid_music_data(self):
        data = MusicData(
            title="Bohemian Rhapsody",
            artist="Queen",
            genre=["Rock", "Opera"],
            credits=[MusicCredit(role="Vocals", name="Freddie Mercury")],
            analysis=[
                {"aspect": "Production", "emoji": "🎛️", "detail": "Groundbreaking multi-track"}
            ],
            structure=[MusicSection(name="Intro", description="Piano opening")],
        )
        assert data.title == "Bohemian Rhapsody"
        assert len(data.credits) == 1
        assert data.analysis[0].aspect == "Production"

    def test_legacy_string_analysis_coercion(self):
        """Legacy string analysis is coerced to a list with a single 'Overview' item."""
        data = MusicData(analysis="A groundbreaking piece")
        assert len(data.analysis) == 1
        assert data.analysis[0].aspect == "Overview"
        assert data.analysis[0].detail == "A groundbreaking piece"

    def test_empty_music_data(self):
        data = MusicData()
        assert data.title == ""
        assert data.genre == []
        assert data.analysis == []


class TestProjectData:
    """Test ProjectData model."""

    def test_valid_project_data(self):
        data = ProjectData.model_validate(
            {
                "projectName": "Bookshelf",
                "difficulty": "beginner",
                "estimatedTime": "4 hours",
                "estimatedCost": "$50",
                "materials": [{"name": "Pine board", "quantity": "2", "cost": "$15"}],
                "tools": [{"name": "Drill", "required": True}],
                "steps": [
                    {
                        "number": 1,
                        "title": "Cut wood",
                        "instruction": "Cut to size",
                        "safetyNote": "Wear goggles",
                    }
                ],
                "safetyWarnings": ["Wear safety glasses"],
            }
        )
        assert data.project_name == "Bookshelf"
        assert data.steps[0].safety_note == "Wear goggles"

    def test_coercion_defaults(self):
        data = ProjectData.model_validate(
            {
                "projectName": None,
                "difficulty": "expert",
                "estimatedTime": None,
            }
        )
        assert data.project_name == "Untitled Project"
        assert data.difficulty == "intermediate"
        assert data.estimated_time == "unknown"


class TestNarrativeData:
    """Test NarrativeData modifier model."""

    def test_valid_narrative_data(self):
        data = NarrativeData.model_validate(
            {
                "keyMoments": [
                    {"description": "The big reveal", "mood": "triumphant", "emoji": "🎉"}
                ],
                "quotes": [{"text": "To be or not to be", "speaker": "Hamlet"}],
                "takeaways": ["Life is short"],
            }
        )
        assert len(data.key_moments) == 1
        assert data.quotes[0].text == "To be or not to be"

    def test_empty_narrative_data(self):
        data = NarrativeData()
        assert data.key_moments == []
        assert data.quotes == []


class TestFinanceData:
    """Test FinanceData modifier model."""

    def test_valid_finance_data(self):
        data = FinanceData.model_validate(
            {
                "costs": [{"item": "Flight", "amount": 500, "currency": "EUR"}],
                "savingTips": ["Book early for cheaper flights"],
            }
        )
        assert len(data.costs) == 1
        assert data.costs[0].amount == 500

    def test_cost_amount_coercion(self):
        cost = FinanceCost(item="Test", amount=None)
        assert cost.amount == 0.0

    def test_finance_ignores_budget_field(self):
        """Finance modifier no longer has a budget field — primary domain owns budget."""
        data = FinanceData.model_validate(
            {
                "costs": [],
                "savingTips": [],
            }
        )
        assert not hasattr(data, "budget")


class TestVIEResponse:
    """Test VIEResponse envelope model."""

    def test_full_response(self):
        response = VIEResponse(
            meta=VIEResponseMeta(
                videoId="abc123",
                videoTitle="Test Video",
                contentTags=["travel", "food"],
                primaryTag="travel",
                userGoal="Plan a trip",
            ),
            tabs=[TabDefinition(id="itinerary", label="Itinerary", dataSource="travel.itinerary")],
            sections=[SectionDefinition(type="day", items=[{"label": "Day 1"}])],
            travel=TravelData(itinerary=[TravelDay(day=1, city="Tokyo")]),
        )
        assert response.meta.video_id == "abc123"
        assert response.travel is not None
        assert response.food is None

    def test_minimal_response(self):
        response = VIEResponse(
            meta=VIEResponseMeta(
                contentTags=["learning"],
                primaryTag="learning",
            ),
        )
        assert response.meta.primary_tag == "learning"
        assert response.travel is None
        assert response.learning is None
        assert response.quizzes is None

    def test_with_modifiers(self):
        response = VIEResponse(
            meta=VIEResponseMeta(
                contentTags=["travel"],
                modifiers=["finance"],
                primaryTag="travel",
            ),
            narrative=NarrativeData(takeaways=["Story lesson"]),
            finance=FinanceData(costs=[FinanceCost(item="Flight", amount=1000, currency="EUR")]),
        )
        assert response.finance.costs[0].amount == 1000
        assert response.narrative.takeaways == ["Story lesson"]

    def test_with_enrichment(self):
        response = VIEResponse(
            meta=VIEResponseMeta(
                contentTags=["learning"],
                primaryTag="learning",
            ),
            quizzes=[{"question": "What?", "options": ["A", "B"]}],
            flashcards=[{"front": "Q", "back": "A"}],
            scenarios=[{"scenario": "Real world"}],
        )
        assert len(response.quizzes) == 1
        assert len(response.flashcards) == 1
        assert len(response.scenarios) == 1

    def test_tab_definition_alias(self):
        tab = TabDefinition.model_validate(
            {
                "id": "test",
                "label": "Test",
                "dataSource": "learning.keyPoints",
            }
        )
        assert tab.data_source == "learning.keyPoints"

    def test_meta_alias(self):
        meta = VIEResponseMeta.model_validate(
            {
                "videoId": "abc",
                "videoTitle": "Test",
                "contentTags": ["learning"],
                "primaryTag": "learning",
                "userGoal": "Learn",
            }
        )
        assert meta.video_id == "abc"
        assert meta.content_tags == ["learning"]


class TestValidateDomainOutput:
    """Tests for validate_domain_output function."""

    def test_single_tag_validates_and_wraps(self):
        result = validate_domain_output(
            ["learning"],
            [],
            {
                "keyPoints": [{"emoji": "💡", "title": "P1", "detail": "D1"}],
                "concepts": [],
                "takeaways": [],
            },
        )
        assert "learning" in result
        assert isinstance(result["learning"], dict)

    def test_single_unknown_tag_returns_empty_dict(self):
        """Unknown tags produce empty dict (no domain model)."""
        result = validate_domain_output(["nonexistent"], [], {"data": "test"})
        # Unknown tag passes through with a warning
        assert "nonexistent" in result

    def test_multi_tag_unwraps_data_wrappers(self):
        result = validate_domain_output(
            ["travel", "food"],
            [],
            {
                "travelData": {"itinerary": [{"day": 1, "city": "Tokyo"}]},
                "foodData": {"ingredients": [{"name": "rice", "amount": 1}]},
            },
        )
        assert "travel" in result
        assert "food" in result

    def test_multi_tag_missing_one_domain_flat_fallback(self):
        """When one domain is wrapped and the other is flat at top level, both should validate."""
        result = validate_domain_output(
            ["travel", "food"],
            [],
            {
                "travelData": {"itinerary": [{"day": 1, "city": "Tokyo"}]},
                # food fields at top level (no foodData wrapper)
                "ingredients": [{"name": "rice", "amount": 1}],
                "steps": [{"number": 1, "instruction": "Cook rice"}],
            },
        )
        assert "travel" in result
        assert "food" in result  # Flat fallback should pick up food fields

    def test_multi_tag_missing_domain_no_data(self):
        """When secondary domain has no data at all, it should not appear in result."""
        result = validate_domain_output(
            ["travel", "food"],
            [],
            {"travelData": {"itinerary": []}},
        )
        assert "travel" in result
        # food has no wrapped key and no flat data — should not be in result

    def test_modifiers_validated(self):
        result = validate_domain_output(
            ["learning"],
            ["finance"],
            {
                "keyPoints": [],
                "concepts": [],
                "takeaways": [],
                "financeData": {"costs": [{"item": "Book", "amount": 20}], "savingTips": []},
            },
        )
        assert "learning" in result
        assert "finance" in result
        assert result["finance"]["costs"][0]["item"] == "Book"

    def test_should_not_invent_domains_when_multi_tag_data_is_empty(self):
        """Truthy model defaults (travel budget, project name) used to pass the
        flat-fallback gate and ship default-only domains."""
        result = validate_domain_output(["travel", "project"], [], {})

        assert result == {}

    def test_should_keep_flat_domain_when_its_fields_are_present(self):
        result = validate_domain_output(["tech", "project"], [], {"topics": ["Rails"]})

        assert list(result) == ["tech"]


class TestSingleTagWrappedOutput:
    """Regression for OuNKBjuV7A4: a single-tag response wrapped in ``{tag}`` or
    ``{tag}Data`` was validated whole against the model, yielding all defaults."""

    _TOPICS = ["DHH on Rails", "Leaving the cloud"]

    def test_should_unwrap_tag_keyed_response(self):
        result = validate_domain_output(["tech"], [], {"tech": {"topics": self._TOPICS}})

        assert result["tech"]["topics"][0] == "DHH on Rails"

    def test_should_unwrap_tag_data_keyed_response(self):
        result = validate_domain_output(["tech"], [], {"techData": {"topics": self._TOPICS}})

        assert result["tech"]["topics"][0] == "DHH on Rails"

    def test_should_validate_flat_response_unchanged(self):
        result = validate_domain_output(["tech"], [], {"topics": self._TOPICS})

        assert result["tech"]["topics"][0] == "DHH on Rails"

    def test_should_keep_modifier_block_next_to_wrapped_domain(self):
        data = {
            "tech": {"topics": self._TOPICS},
            "financeData": {"costs": [{"item": "Server", "amount": 9}], "savingTips": []},
        }

        result = validate_domain_output(["tech"], ["finance"], data)

        assert result["finance"]["costs"][0]["item"] == "Server"

    def test_learning_typed_connections_and_group_survive_validation(self):
        """Regression: typed connections + `group` must round-trip through the
        full validate path. Before widening the model these triggered a
        ValidationError → whole-domain passthrough (connections kept but `group`
        and every other field's normalization lost)."""
        result = validate_domain_output(
            ["learning"],
            [],
            {
                "keyPoints": [],
                "takeaways": [],
                "concepts": [
                    {
                        "name": "Compound interest",
                        "emoji": "💵",
                        "definition": "d",
                        "group": "Foundations",
                        "connections": [{"to": "Time value of money", "type": "causes"}],
                    }
                ],
            },
        )
        concept = result["learning"]["concepts"][0]
        assert concept["group"] == "Foundations"
        assert concept["connections"] == [{"to": "Time value of money", "type": "causes"}]

    def test_invalid_data_passes_through_with_warning(self):
        """Invalid data is passed through (not raised) — validation is lenient."""
        # validate_domain_output catches errors and passes data through
        result = validate_domain_output(
            ["food"],
            [],
            {"steps": [{"number": "not_a_number", "instruction": 123}]},
        )
        # Should still return data (passed through despite validation issues)
        assert "food" in result


class TestMultiTagFlatFallbackFailure:
    """Regression (p0.6 review #1): a missing tag whose flat validation fails
    used to receive the WHOLE raw response (tech got ``foodData``)."""

    _DATA = {"foodData": {"steps": []}, "topics": 5}

    def test_should_skip_tag_when_its_flat_validation_fails(self):
        result = validate_domain_output(["tech", "food"], [], self._DATA)

        assert "tech" not in result

    def test_should_keep_wrapped_tag_when_other_tags_flat_validation_fails(self):
        result = validate_domain_output(["tech", "food"], [], self._DATA)

        assert result["food"]["steps"] == []


class TestMixedWrappedAndFlatShape:
    """Regression (p0.6 review #2): a tag with BOTH a wrapped block and flat
    top-level fields (chunked merge of a wrapped batch with a flat one) used
    to drop one half silently."""

    def test_should_concatenate_lists_when_single_tag_is_both_wrapped_and_flat(self):
        data = {"tech": {"topics": ["Wrapped"]}, "topics": ["Flat"]}

        result = validate_domain_output(["tech"], [], data)

        assert result["tech"]["topics"] == ["Wrapped", "Flat"]

    def test_should_keep_wrapper_only_fields_when_single_tag_is_also_flat(self):
        data = {"techData": {"languages": ["Ruby"]}, "topics": ["Flat"]}

        result = validate_domain_output(["tech"], [], data)

        assert result["tech"]["languages"] == ["Ruby"]

    def test_should_not_duplicate_list_items_present_in_both_shapes(self):
        data = {"tech": {"topics": ["Same"]}, "topics": ["Same", "New"]}

        result = validate_domain_output(["tech"], [], data)

        assert result["tech"]["topics"] == ["Same", "New"]

    def test_should_merge_flat_fields_into_wrapped_block_when_multi_tag(self):
        data = {
            "techData": {"topics": ["Wrapped"]},
            "frameworks": ["Rails"],
            "learningData": {"takeaways": ["T1"]},
        }

        result = validate_domain_output(["tech", "learning"], [], data)

        assert result["tech"]["frameworks"] == ["Rails"]

    def test_should_keep_wrapper_value_when_scalar_fields_conflict(self):
        data = {
            "scienceData": {"field": "Physics"},
            "field": "Chemistry",
            "learningData": {"takeaways": ["T1"]},
        }

        result = validate_domain_output(["science", "learning"], [], data)

        assert result["science"]["field"] == "Physics"

    def test_should_take_flat_value_when_wrapper_value_is_empty(self):
        data = {"scienceData": {"field": ""}, "field": "Chemistry", "learningData": {"x": 1}}

        result = validate_domain_output(["science", "learning"], [], data)

        assert result["science"]["field"] == "Chemistry"

    def test_should_not_merge_flat_key_shared_with_another_content_tag(self):
        concept = {"name": "Entropy", "emoji": "🔥", "definition": "d"}
        data = {
            "scienceData": {"field": "Physics"},
            "learningData": {"takeaways": ["T1"]},
            "concepts": [concept],
        }

        result = validate_domain_output(["science", "learning"], [], data)

        assert result["science"]["concepts"] == []

    def test_should_warn_when_wrapped_and_flat_shapes_are_merged(self, caplog):
        data = {"tech": {"topics": ["Wrapped"]}, "topics": ["Flat"]}

        with caplog.at_level(logging.WARNING, logger="src.models.domain_types"):
            validate_domain_output(["tech"], [], data)

        assert any("wrapped block AND flat fields" in r.message for r in caplog.records)

    def test_should_not_warn_when_multi_tag_response_is_fully_wrapped(self, caplog):
        data = {"techData": {"topics": ["A"]}, "learningData": {"takeaways": ["T1"]}}

        with caplog.at_level(logging.WARNING, logger="src.models.domain_types"):
            validate_domain_output(["tech", "learning"], [], data)

        assert not any("flat fields" in r.message for r in caplog.records)


class TestModelRegistries:
    """Test model registry completeness."""

    def test_all_eight_domains_registered(self):
        expected = {
            "travel",
            "food",
            "learning",
            "review",
            "tech",
            "fitness",
            "music",
            "project",
            "language",
            "science",
            "podcast",
            "news",
            "gaming",
            "sport",
        }
        assert set(DOMAIN_MODELS.keys()) == expected

    def test_all_modifiers_registered(self):
        assert set(MODIFIER_MODELS.keys()) == {"narrative", "finance"}

    def test_valid_content_tags_match_domains(self):
        assert VALID_CONTENT_TAGS == frozenset(DOMAIN_MODELS.keys())

    def test_valid_modifiers_match_modifier_models(self):
        assert VALID_MODIFIERS == frozenset(MODIFIER_MODELS.keys())
