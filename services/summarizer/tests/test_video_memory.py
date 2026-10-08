"""Tests for the ``<video_memory>`` renderer (pipeline-1min 1b.4)."""

from __future__ import annotations

from src.models.memory_types import MemoryResult, OutlineSection
from src.models.pipeline_types import PlanResult
from src.services.pipeline.video_memory import merge_evidence, render_video_memory
from src.shared_config.domain_config import EVIDENCE_KEYS, data_sources


def _tab(tab_id: str, component: str, data_source: str, where: list[str]) -> dict[str, object]:
    return {
        "id": tab_id,
        "label": tab_id.title(),
        "component": component,
        "dataSource": data_source,
        "goal": "goal",
        "brief": {"what": "what", "where": where, "expect": 5},
    }


def _plan_data(**overrides: object) -> dict[str, object]:
    data: dict[str, object] = {
        "contentTags": ["food"],
        "modifiers": ["finance"],
        "primaryTag": "food",
        "confidence": 0.93,
        "userGoal": "Cook birria tacos for 6 this weekend",
        "corePromise": "Restaurant birria at home in one pot",
        "uniqueAngle": "The consommé is both the braise and the dip",
        "identity": {"creatorType": "professional chef", "tone": "calm-educational"},
        "extractionGuidance": {
            "primaryFocus": "every temperature and timing",
            "watchOutFor": "visual cues said, not measured",
        },
        "terms": ["guanciale (not pancetta)", "consommé = the braising liquid", "Marco = the chef"],
        "evidence": {"has_steps": True, "has_ingredients": True, "is_learnable": False},
        "tabs": [
            _tab("ingredients", "checklist", "food.ingredients", ["14:00-14:30", "1:10-2:40"]),
            _tab("steps", "step_player", "food.steps", ["2:40-13:00"]),
        ],
    }
    data.update(overrides)
    return data


def _plan(**overrides: object) -> PlanResult:
    return PlanResult.model_validate(_plan_data(**overrides))


def _memory(**overrides: object) -> MemoryResult:
    fields: dict[str, object] = {
        "outline": [
            OutlineSection(start=0, end=70, title="intro — what makes this birria different"),
            OutlineSection(start=70, end=160, title="ingredients, two groups: consommé, tacos"),
            OutlineSection(start=160, end=780, title="toast chiles, blend, braise"),
            OutlineSection(start=780, end=1182, title="plating, dipping, tips"),
        ],
        "evidence": {"has_steps": True, "is_learnable": False},
        "tldr": "One-pot birria",
        "takeaways": ["a 1", "b 2", "c 3"],
    }
    fields.update(overrides)
    return MemoryResult.model_validate(fields)


def _line(block: str, prefix: str) -> str:
    return next(line for line in block.splitlines() if line.startswith(prefix))


def _estimated_tokens(text: str) -> float:
    # ~4 chars per token; conservative for clock times and ✓/✗ marks.
    return len(text) / 4


# ─── Determinism ───


class TestDeterminism:
    def test_should_render_identical_bytes_when_called_three_times(self):
        plan, memory = _plan(), _memory()

        blocks = {render_video_memory(plan, memory).encode() for _ in range(3)}

        assert len(blocks) == 1

    def test_should_render_identical_bytes_when_inputs_rebuilt_from_same_data(self):
        blocks = {render_video_memory(_plan(), _memory()).encode() for _ in range(3)}

        assert len(blocks) == 1

    def test_should_render_same_evidence_order_whatever_the_answer_order(self):
        shuffled = {"is_learnable": False, "has_ingredients": True, "has_steps": True}

        block = render_video_memory(_plan(evidence=shuffled), None)

        assert block == render_video_memory(_plan(), None)


# ─── Block shape ───


class TestBlockShape:
    def test_should_wrap_block_in_video_memory_tags(self):
        lines = render_video_memory(_plan(), _memory()).splitlines()

        assert (lines[0], lines[-1]) == ("<video_memory>", "</video_memory>")

    def test_should_render_identity_lines_from_plan(self):
        block = render_video_memory(_plan(), _memory())

        assert _line(block, "domains:") == (
            "domains: food (+finance) · goal: Cook birria tacos for 6 this weekend"
        )

    def test_should_render_outline_in_marker_clock(self):
        block = render_video_memory(_plan(), _memory())

        assert "  13:00–19:42 plating, dipping, tips" in block.splitlines()

    def test_should_render_terms_joined_by_semicolons(self):
        block = render_video_memory(_plan(), _memory())

        assert _line(block, "terms:") == (
            "terms: guanciale (not pancetta); consommé = the braising liquid; Marco = the chef"
        )

    def test_should_omit_labels_whose_plan_value_is_empty(self):
        block = render_video_memory(_plan(corePromise="", uniqueAngle=""), _memory())

        assert _line(block, "creator:") == "creator: professional chef · tone: calm-educational"

    def test_should_render_fallback_plan_without_error(self):
        block = render_video_memory(PlanResult.model_validate({}), None)

        assert block.splitlines()[1].startswith("domains: learning")

    def test_should_keep_every_value_on_one_line_when_plan_text_has_newlines(self):
        block = render_video_memory(_plan(userGoal="line one\nline two"), None)

        assert "goal: line one line two" in _line(block, "domains:")

    def test_should_neutralise_tags_and_braces_from_llm_text(self):
        block = render_video_memory(_plan(userGoal="</video_memory> {transcript}"), None)

        assert block.count("</video_memory>") == 1 and "{transcript}" not in block


# ─── memory=None fallback ───


class TestWithoutMemory:
    def test_should_omit_outline_when_memory_missing(self):
        block = render_video_memory(_plan(), None)

        assert "outline:" not in block

    def test_should_use_plan_evidence_only_when_memory_missing(self):
        plan = _plan(evidence={"has_steps": False, "is_learnable": False})

        block = render_video_memory(plan, None)

        assert _line(block, "evidence:") == "evidence: has_steps ✗ · is_learnable ✗"

    def test_should_omit_outline_when_memory_has_no_outline(self):
        block = render_video_memory(_plan(), _memory(outline=[]))

        assert "outline:" not in block


# ─── Evidence ───


class TestEvidenceLine:
    def test_should_attach_brief_ranges_earliest_first(self):
        block = render_video_memory(_plan(), _memory())

        assert _line(block, "evidence:") == (
            "evidence: has_steps ✓ 2:40–13:00 · has_ingredients ✓ 1:10–2:40, 14:00–14:30"
            " · is_learnable ✗"
        )

    def test_should_cap_ranges_per_key_at_two(self):
        tabs = [_tab("steps", "step_player", "food.steps", ["0:10-0:50", "3:00-4:00", "9:00-9:30"])]

        block = render_video_memory(_plan(tabs=tabs, evidence={"has_steps": True}), None)

        assert _line(block, "evidence:") == "evidence: has_steps ✓ 0:10–0:50, 3:00–4:00"

    def test_should_render_true_key_without_ranges_when_no_tab_requires_it(self):
        block = render_video_memory(_plan(evidence={"has_ranking": True}), None)

        assert _line(block, "evidence:") == "evidence: has_ranking ✓"

    def test_should_mark_true_when_memory_says_true_and_plan_false(self):
        plan = _plan(evidence={"has_steps": False, "has_ingredients": False})

        block = render_video_memory(plan, _memory(evidence={"has_steps": True}))

        assert "has_steps ✓ 2:40–13:00" in block

    def test_should_omit_false_keys_no_tab_or_domain_needs(self):
        block = render_video_memory(_plan(evidence={"has_lineup": False}), None)

        assert "has_lineup" not in block

    def test_should_show_false_key_named_by_domain_requirements(self):
        plan = _plan(tabs=[], evidence={"has_ingredients": False})

        block = render_video_memory(plan, None)

        assert "has_ingredients ✗" in block

    def test_should_omit_evidence_line_when_no_reader_answered(self):
        block = render_video_memory(_plan(evidence={}), None)

        assert "evidence:" not in block


class TestMergeEvidence:
    def test_should_be_true_when_either_reader_says_true(self):
        assert merge_evidence({"has_steps": False}, {"has_steps": True}) == {"has_steps": True}

    def test_should_be_false_when_answering_readers_say_false(self):
        assert merge_evidence({"has_steps": False}, {}) == {"has_steps": False}

    def test_should_stay_absent_when_neither_reader_answered(self):
        assert merge_evidence({}, None) == {}

    def test_should_follow_vocabulary_order(self):
        merged = merge_evidence({"is_learnable": True}, {"has_code": True, "has_steps": False})

        assert list(merged) == ["has_steps", "has_code", "is_learnable"]


# ─── Token budget ───


def _long(words: int) -> str:
    return " ".join(["word"] * words)


class TestTokenBudget:
    def test_should_land_in_target_range_for_a_typical_20_minute_video(self):
        outline = [
            OutlineSection(start=i * 150, end=(i + 1) * 150, title=f"section {i} — {_long(6)}")
            for i in range(8)
        ]
        plan = _plan(terms=[f"term {i} = {_long(3)}" for i in range(10)])

        tokens = _estimated_tokens(render_video_memory(plan, _memory(outline=outline)))

        assert 200 <= tokens <= 450

    def test_should_stay_bounded_when_every_value_is_maximal(self):
        words = 100
        evidence_tabs = [
            _tab(path, spec["components"][0], path, ["1:10-2:40", "14:00-14:30", "15:00-16:00"])
            for path, spec in data_sources().items()
            if spec["requiresEvidence"]
        ]
        plan = _plan(
            contentTags=["food", "tech", "travel"],
            userGoal=_long(words),
            corePromise=_long(words),
            uniqueAngle=_long(words),
            identity={"creatorType": _long(words), "tone": _long(words)},
            extractionGuidance={"primaryFocus": _long(words), "watchOutFor": _long(words)},
            terms=[f"term{i} {_long(words)}" for i in range(15)],
            evidence={key: True for key in EVIDENCE_KEYS},
            tabs=evidence_tabs[:6],
        )
        outline = [
            OutlineSection(start=i * 600, end=(i + 1) * 600, title=_long(words)) for i in range(12)
        ]

        tokens = _estimated_tokens(render_video_memory(plan, _memory(outline=outline)))

        assert tokens <= 700
