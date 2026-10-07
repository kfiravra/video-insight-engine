"""Plan prompt render (pipeline-1min 1b.2, Appendix B.2).

The planner reads the FULL marked transcript (no 3,000-char preview), gets an
explicit output-language block (D16), registry-rendered caps (one per
dataSource) and domain requirements conditional on its own evidence (D15),
and six examples in the new output schema.
"""

from __future__ import annotations

import json
import re
from collections.abc import Iterator
from unittest.mock import patch

import pytest

from src.models.pipeline_types import PlanResult
from src.services.pipeline import plan_prompt
from src.services.pipeline.plan_prompt import PlanVideo, render_plan_prompt
from src.shared_config.domain_config import (
    EVIDENCE_KEYS,
    NON_EXTRACTION_DATASOURCES,
    data_source,
    data_sources,
    domain_requirements,
    effective_requirements,
    requirement_evidence,
    valid_components,
)


@pytest.fixture(autouse=True)
def disk_prompts() -> Iterator[None]:
    """Render from the files on disk, never a registry version."""
    with (
        patch.object(plan_prompt, "_load_plan_prompt", lambda: plan_prompt.PROMPT_PATH.read_text()),
        patch.object(
            plan_prompt,
            "_load_component_toolkit",
            lambda: plan_prompt.COMPONENT_TOOLKIT_PATH.read_text(),
        ),
    ):
        yield


def _video(**overrides: object) -> PlanVideo:
    fields: dict = {
        "title": "How to Make Pissalandrea",
        "channel": "Pasta Grannies",
        "description": "Focaccia from Liguria.",
        "duration": 675,
        "category_hint": "food",
        "content_format": "tutorial",
        "transcript": "[0:00] The village.\n[0:21] Her story.",
        "probe_hint": None,
    }
    return PlanVideo(**{**fields, **overrides})


def _block(prompt: str, tag: str) -> str:
    """The body of the LAST ``<tag>…</tag>`` block (prose may name the tag earlier)."""
    start = prompt.rindex(f"<{tag}>") + len(tag) + 2
    return prompt[start : prompt.index(f"</{tag}>", start)]


class TestOutputLanguage:
    def test_should_state_english_output_when_rendering(self) -> None:
        block = _block(render_plan_prompt(_video()), "output_language")

        assert "in ENGLISH, whatever language the transcript is in" in block

    def test_should_cover_labels_and_goals_when_stating_language(self) -> None:
        block = _block(render_plan_prompt(_video()), "output_language")

        assert all(word in block for word in ("tab labels", "goals", "briefs", "terms"))


class TestTranscript:
    def test_should_include_the_whole_transcript_when_it_is_long(self) -> None:
        transcript = "\n".join(f"[{i // 3}:{(i % 3) * 20:02d}] line {i}" for i in range(600))

        prompt = render_plan_prompt(_video(transcript=transcript))

        assert _block(prompt, "transcript").strip() == transcript

    def test_should_not_render_a_preview_block_when_rendering(self) -> None:
        assert "<transcript_preview>" not in render_plan_prompt(_video())

    def test_should_say_no_transcript_when_it_is_empty(self) -> None:
        prompt = render_plan_prompt(_video(transcript="  "))

        assert "no transcript" in _block(prompt, "transcript")


class TestVideoBlock:
    def test_should_render_one_hint_line_when_probe_hint_is_set(self) -> None:
        video = _block(render_plan_prompt(_video(probe_hint="domain=food")), "video")

        assert video.strip().splitlines()[3] == "Hint: domain=food"

    def test_should_omit_hint_without_blank_line_when_probe_hint_is_unset(self) -> None:
        video = _block(render_plan_prompt(_video(probe_hint="  ")), "video")

        assert "Hint:" not in video and "\n\n" not in video

    def test_should_order_fields_as_specified_when_playbook_applies(self) -> None:
        prompt = render_plan_prompt(
            _video(category_hint="gaming", content_format="unboxing", probe_hint="domain=gaming")
        )

        order = [
            "Title:",
            "Channel:",
            "Duration:",
            "Hint:",
            "<playbook",
            "Description:",
            "<transcript>",
        ]
        positions = [prompt.rindex(marker) for marker in order]
        assert positions == sorted(positions)

    def test_should_drop_classifier_trait_lines_when_rendering(self) -> None:
        video = _block(render_plan_prompt(_video()), "video")

        assert not any(label in video for label in ("Content Traits:", "Category:", "Format:"))


class TestExtractionCaps:
    def test_should_render_one_cap_per_registry_datasource_when_rendering(self) -> None:
        block = _block(render_plan_prompt(_video()), "extraction_caps")
        lines = {line.split(":", 1)[0].lstrip("- "): line for line in block.splitlines()}

        missing = [
            path
            for path, spec in data_sources().items()
            if spec["kind"] == "list"
            and f"{spec['field']} ≤ {spec['cap']}" not in lines[spec["domain"]]
        ]
        assert missing == []

    def test_should_follow_the_registry_when_a_cap_changes(self) -> None:
        registry = data_sources()
        registry["food.ingredients"]["cap"] = 7
        with patch("src.shared_config.domain_config._registry", lambda: registry):
            block = _block(render_plan_prompt(_video()), "extraction_caps")

        assert "ingredients ≤ 7" in block

    def test_should_mark_object_paths_when_rendering(self) -> None:
        block = _block(render_plan_prompt(_video()), "extraction_caps")

        assert "budget (one object)" in block


class TestDomainRequirements:
    def test_should_make_food_requirements_conditional_when_rendering(self) -> None:
        block = _block(render_plan_prompt(_video()), "domain_requirements")
        food = next(line for line in block.splitlines() if line.startswith("- food:"))

        assert (
            "`checklist` only when has_ingredients is true" in food
            and "`step_player` only when has_steps is true" in food
        )

    def test_should_keep_ungated_requirement_unconditional_when_rendering(self) -> None:
        block = _block(render_plan_prompt(_video()), "domain_requirements")

        assert "- podcast: `moment_track` always" in block

    def test_should_render_every_required_component_when_rendering(self) -> None:
        block = _block(render_plan_prompt(_video()), "domain_requirements")

        missing = [
            (domain, component)
            for domain, rules in domain_requirements().items()
            for component in rules.get("required", [])
            if f"`{component}`"
            not in next(
                (line for line in block.splitlines() if line.startswith(f"- {domain}:")), ""
            )
        ]
        assert missing == []


def _evidence_rules(prompt_file: str) -> str:
    text = (plan_prompt.PROMPT_PATH.parent / prompt_file).read_text()
    start = text.index("\n<evidence_rules>\n") + 1
    return text[start : text.index("\n", text.index("- is_learnable:", start))]


def test_should_define_evidence_like_memory_when_prompt_is_read() -> None:
    """Plan and memory answer the same keys with the same meaning — reconcile
    (phase 3) demotes only when BOTH say false."""
    assert _evidence_rules("plan.txt") == _evidence_rules("memory.txt")


class TestRemovedInstructions:
    @pytest.mark.parametrize(
        "removed",
        [
            "<outbound_links_instructions>",
            "outboundLinks",
            "itemCounts",
            '"reasoning"',
            '"audience"',
            '"enrich": true',
            'Always include "learning"',
            "AND enrichment.scenarios",
            "absorbed by quiz_arena",
            "{transcript_preview}",
            "{content_traits}",
        ],
    )
    def test_should_not_carry_retired_instruction_when_prompt_is_read(self, removed: str) -> None:
        assert removed not in plan_prompt.PROMPT_PATH.read_text()

    def test_should_ask_for_three_to_six_supported_tabs_when_rendering(self) -> None:
        assert "3–6 tabs, only tabs the content supports" in render_plan_prompt(_video())

    def test_should_route_formats_on_plan_evidence_when_rendering(self) -> None:
        block = _block(render_plan_prompt(_video()), "format_routing")

        assert "YOUR evidence" in block and "has_narrative" not in block


# ─── The six examples follow the new schema ────────────────────────────


def _examples() -> list[dict]:
    text = plan_prompt.PROMPT_PATH.read_text()
    bodies = re.findall(r"<example>\n[^\n]*\n(.*?)\n</example>", text, flags=re.DOTALL)
    return [json.loads(body.replace("{{", "{").replace("}}", "}")) for body in bodies]


_EXAMPLES = _examples()
_IDS = [ex["tabs"][0]["id"] + "-" + ex["primaryTag"] for ex in _EXAMPLES]


def test_should_ship_six_examples_when_prompt_is_read() -> None:
    assert len(_EXAMPLES) == 6


@pytest.mark.parametrize("example", _EXAMPLES, ids=_IDS)
class TestExamples:
    def test_should_answer_every_evidence_key_when_showing_a_plan(self, example: dict) -> None:
        assert set(example["evidence"]) == set(EVIDENCE_KEYS)

    def test_should_drop_retired_fields_when_showing_a_plan(self, example: dict) -> None:
        keys = set(example) | set(example["identity"]) | {k for t in example["tabs"] for k in t}
        assert keys.isdisjoint({"reasoning", "itemCounts", "outboundLinks", "audience"})

    def test_should_plan_three_to_six_tabs_when_showing_a_plan(self, example: dict) -> None:
        assert 3 <= len(example["tabs"]) <= 6

    def test_should_brief_every_tab_within_its_cap_when_showing_a_plan(self, example: dict) -> None:
        over = [
            tab["id"]
            for tab in PlanResult.model_validate(example).tabs
            if not tab["brief"]["what"]
            or not tab["brief"]["where"]
            or tab["dataSource"] not in NON_EXTRACTION_DATASOURCES
            and tab["brief"]["expect"] > (data_source(tab["dataSource"]) or {"cap": 0})["cap"]
        ]
        assert over == []

    def test_should_use_registered_paths_and_valid_components_when_showing_a_plan(
        self, example: dict
    ) -> None:
        bad = [
            tab["id"]
            for tab in example["tabs"]
            if tab["component"] not in valid_components()
            or (
                tab["dataSource"] not in NON_EXTRACTION_DATASOURCES
                and data_source(tab["dataSource"]) is None
            )
        ]
        assert bad == []

    def test_should_meet_conditional_requirements_when_showing_a_plan(self, example: dict) -> None:
        domain = example["primaryTag"]
        required = effective_requirements(domain, None, evidence=example["evidence"])["required"]
        planned = {tab["component"] for tab in example["tabs"]}

        assert set(required) <= planned

    def test_should_place_quiz_last_when_showing_a_plan(self, example: dict) -> None:
        components = [tab["component"] for tab in example["tabs"]]
        assert "quiz_arena" not in components[:-1]

    def test_should_gate_only_on_true_evidence_when_showing_a_plan(self, example: dict) -> None:
        gated = requirement_evidence().get(example["primaryTag"], {})
        planned = {tab["component"] for tab in example["tabs"]}

        assert all(example["evidence"][key] for comp, key in gated.items() if comp in planned)
