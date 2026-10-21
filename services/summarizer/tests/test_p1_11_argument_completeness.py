"""p1.11 — the argument-completeness rule and its talk / podcast examples (output-quality).

Block 1 (every group of a run, cached 1h) gains the argument rule right after
the measured completeness rule; the two worked examples — S01's key idea at
40:40, S02's position from its last ten minutes — ride in block 3 only, for
the group extracting ``learning.keyIdeas`` in a run whose primary domain is
the example's. The single call (``EXTRACTION_PARALLEL=false``) sees neither;
it changes only by the fresh component map in ``base_extraction.txt`` and the
no-invention line in ``quality_rules.txt``, which both paths read.
"""

from __future__ import annotations

import json
import re
from unittest.mock import MagicMock, patch

import pytest

from src.services.pipeline import extraction_group_prompt
from src.services.pipeline.extraction_chunks import ExtractionChunk
from src.services.pipeline.extraction_group_prompt import (
    GROUP_RULES_PATH,
    JOB_TEMPLATE_PATH,
    GroupJob,
    argument_examples,
    build_group_messages,
    completeness_rule,
    job_block,
    with_group_rules,
)
from src.services.pipeline.extraction_prompt import (
    ExtractionPrompt,
    ExtractionPromptInput,
    build_extraction_prompt,
)
from src.services.pipeline.field_groups import ExtractionGroup
from src.services.pipeline.field_requests import FieldRequest
from src.services.pipeline.prompt_builder import PROMPTS_DIR, load_prompt_text
from src.shared_config.domain_config import valid_components

# The rule 4.0d round 2 measured (scripts/_model_ab_variants.py) — byte-identical since.
_MEASURED_RULE = (
    "Completeness: emit every distinct step, item or command the transcript supports "
    "— never merge two into one, never skip one."
)
_ARGUMENT_RULE = (
    "Arguments the same way: every distinct claim, its reasoning and the sentence that "
    "carries it — never merge two claims into one, never skip the last 10 minutes (the close "
    "is where a talk lands its ask and a conversation its verdict)."
)
_NUMBERS_RULE = (
    "Never invent a number: a duration, rest time, quantity, count or score the speaker does "
    "not state stays null — complete means every item the transcript supports, never a figure "
    "it does not."
)
_TALK_OPEN = '<argument_example field="learning.keyIdeas" primary="learning">'
_PODCAST_OPEN = '<argument_example field="learning.keyIdeas" primary="podcast">'
_S01_QUOTE = (
    "Writing code by hand. Is no longer an economically productive enterprise for the "
    "vast majority of programmers working at the vast majority of companies."
)
_S02_QUOTE = "don't start thinking about the risk start thinking about the goals"
_KEY_IDEA_KEYS = {"claim", "why", "quote", "timestamp", "section", "kind"}
_KINDS = {"thesis", "argument", "announcement", "number", "story", "ask"}
_NO_INVENTION = "Never state a duration, rest time, quantity or number the speaker did not say"
# Retired names the stale map carried (L15) — none may survive in either path.
_RETIRED = ["FlashDeck", "CodeExplorer", "ExerciseTracker", "Verdict (score ring", "MomentTrack"]
# A renderer id (component-registry.tsx) the registry's component list gains with 1.1/1.2.
_RENDERER_ONLY = {"stat_banner"}

_IDEAS_TAB = {
    "id": "ideas",
    "label": "Key ideas",
    "component": "spot_explorer",
    "dataSource": "learning.keyIdeas",
}
_CONCEPTS_TAB = {
    "id": "concepts",
    "label": "Concepts",
    "component": "concept_canvas",
    "dataSource": "learning.concepts",
}
_CHUNK = ExtractionChunk(0, "[0:00] hi")


def _field(field: str, *, tab: str) -> FieldRequest:
    return FieldRequest("learning", field, "list", "planned", (tab,), 20, 12, 120, False)


def _group(group_id: str, *fields: FieldRequest) -> ExtractionGroup:
    tab_ids = tuple(dict.fromkeys(t for f in fields for t in f.tab_ids))
    return ExtractionGroup(group_id, fields, False, 1440, tab_ids)


_IDEAS = _group("text1", _field("keyIdeas", tab="ideas"))
_CONCEPTS = _group("text2", _field("concepts", tab="concepts"))


def _tags(primary: str) -> list[str]:
    return ["learning"] if primary == "learning" else [primary, "learning"]


def _prompt(primary: str = "learning") -> ExtractionPrompt:
    return build_extraction_prompt(
        ExtractionPromptInput(
            _tags(primary),
            [],
            "quality rules text",
            primary_tag=primary,
            title="T",
            duration_seconds=3787,
            tabs=[_IDEAS_TAB, _CONCEPTS_TAB],
        )
    )


def _job(primary: str) -> GroupJob:
    return GroupJob([_IDEAS_TAB, _CONCEPTS_TAB], _tags(primary), primary)


def _rules() -> str:
    return load_prompt_text(GROUP_RULES_PATH)


def _job_text(group: ExtractionGroup, primary: str) -> str:
    """Block 3 as the split path sends it: the ruled prompt's tail feeds the job block."""
    ruled = with_group_rules(_prompt(primary))
    return build_group_messages(ruled, group, _CHUNK, _job(primary))[2]["text"]


def _example_item(primary: str) -> dict[str, object]:
    """The JSON item of the example rendered for the key-ideas group of ``primary``."""
    line = next(ln for ln in _job_text(_IDEAS, primary).splitlines() if ln.startswith('{"claim"'))
    return json.loads(line)


def _single_call_system() -> str:
    """The rules today's single call sends, with the shipped quality rules."""
    rules = load_prompt_text(PROMPTS_DIR / "quality_rules.txt")
    return build_extraction_prompt(ExtractionPromptInput(["food"], [], rules, title="T")).system


def _component_map(system: str) -> str:
    return system[system.index("Your data powers") : system.index("</instructions>")]


class TestRulesFileShares:
    _RULES = 'rule line\n\n<argument_example field="a.b" primary="x">\nbody\n</argument_example>\n'

    def test_should_keep_only_the_rule_for_block_1_when_the_file_has_examples(self) -> None:
        assert completeness_rule(self._RULES) == "rule line"

    def test_should_keep_only_the_examples_for_block_3_when_the_file_has_them(self) -> None:
        assert argument_examples(self._RULES) == (
            '<argument_example field="a.b" primary="x">\nbody\n</argument_example>'
        )

    def test_should_find_no_example_when_the_file_is_a_bare_rule(self) -> None:
        assert argument_examples("rule only\n") == ""


class TestBlockOneRule:
    def test_should_keep_the_measured_rule_byte_for_byte_when_the_argument_rule_is_added(
        self,
    ) -> None:
        assert completeness_rule(_rules()).splitlines()[0] == _MEASURED_RULE

    def test_should_end_the_rules_with_the_argument_rule_when_the_run_is_split(self) -> None:
        system = with_group_rules(_prompt()).system

        assert (
            f"\n\n{_MEASURED_RULE}\n{_ARGUMENT_RULE}\n{_NUMBERS_RULE}\n</quality_rules>" in system
        )

    def test_should_repeat_never_invent_numbers_in_the_completeness_rule(self) -> None:
        # Kfir 2026-10-21 (D13): S06's extraction wrote "rest 10 minutes" for "give it a sec".
        assert _NUMBERS_RULE in completeness_rule(_rules())

    def test_should_keep_the_argument_examples_out_of_block_1(self) -> None:
        assert "<argument_example" not in with_group_rules(_prompt()).system

    def test_should_carry_the_examples_at_the_end_of_the_ruled_tail(self) -> None:
        prompt = _prompt()

        assert with_group_rules(prompt).tail == (
            f"{prompt.tail.rstrip()}\n\n{argument_examples(_rules())}\n"
        )

    def test_should_read_the_rules_file_once_when_the_prompt_is_ruled(self) -> None:
        reads = MagicMock(wraps=load_prompt_text)

        with patch.object(extraction_group_prompt, "load_prompt_text", reads):
            with_group_rules(_prompt())

        assert reads.call_count == 1

    def test_should_keep_the_argument_rule_out_of_the_single_call(self) -> None:
        assert "Arguments the same way" not in _prompt().system


class TestArgumentExamples:
    def test_should_render_the_talk_example_when_a_learning_run_extracts_key_ideas(self) -> None:
        text = _job_text(_IDEAS, "learning")

        assert (_TALK_OPEN in text, _PODCAST_OPEN in text) == (True, False)

    def test_should_render_the_podcast_example_when_a_podcast_run_extracts_key_ideas(
        self,
    ) -> None:
        text = _job_text(_IDEAS, "podcast")

        assert (_PODCAST_OPEN in text, _TALK_OPEN in text) == (True, False)

    def test_should_render_no_example_when_the_group_extracts_no_key_ideas(self) -> None:
        assert "<argument_example" not in _job_text(_CONCEPTS, "learning")

    def test_should_render_no_example_when_the_primary_is_neither_learning_nor_podcast(
        self,
    ) -> None:
        assert "<argument_example" not in _job_text(_IDEAS, "science")

    def test_should_send_todays_job_bytes_when_no_example_applies(self) -> None:
        template, examples = load_prompt_text(JOB_TEMPLATE_PATH), argument_examples(_rules())
        bare = job_block(_CONCEPTS, _CHUNK, _job("learning"), template)

        assert job_block(_CONCEPTS, _CHUNK, _job("learning"), template, examples) == bare

    def test_should_put_the_example_after_the_job_element(self) -> None:
        text = _job_text(_IDEAS, "learning")

        assert text.index("</your_job>") < text.index(_TALK_OPEN)

    def test_should_keep_the_examples_out_of_the_cached_blocks(self) -> None:
        blocks = build_group_messages(with_group_rules(_prompt()), _IDEAS, _CHUNK, _job("learning"))

        assert "<argument_example" not in blocks[0]["text"] + blocks[1]["text"]

    def test_should_quote_s01_verbatim_in_the_talk_example(self) -> None:
        assert _S01_QUOTE in _job_text(_IDEAS, "learning")

    def test_should_quote_s02_verbatim_in_the_podcast_example(self) -> None:
        assert _S02_QUOTE in _job_text(_IDEAS, "podcast")


class TestExampleShape:
    @pytest.mark.parametrize("primary", ["learning", "podcast"])
    def test_should_shape_the_example_as_a_key_ideas_item(self, primary: str) -> None:
        # `speaker` is the item's optional field (who holds a podcast position).
        assert _KEY_IDEA_KEYS <= set(_example_item(primary)) <= _KEY_IDEA_KEYS | {"speaker"}

    @pytest.mark.parametrize("primary", ["learning", "podcast"])
    def test_should_quote_at_most_25_words(self, primary: str) -> None:
        assert len(str(_example_item(primary)["quote"]).split()) <= 25

    @pytest.mark.parametrize(("primary", "second"), [("learning", 2440), ("podcast", 4453)])
    def test_should_stamp_the_second_the_line_is_spoken(self, primary: str, second: int) -> None:
        assert _example_item(primary)["timestamp"] == second

    @pytest.mark.parametrize("primary", ["learning", "podcast"])
    def test_should_use_a_registered_kind(self, primary: str) -> None:
        assert _example_item(primary)["kind"] in _KINDS

    def test_should_name_who_holds_the_position_in_speaker_when_the_example_is_a_podcast(
        self,
    ) -> None:
        assert _example_item("podcast")["speaker"] == "Max Tegmark"

    def test_should_keep_the_name_out_of_the_claim_when_the_example_is_a_podcast(self) -> None:
        # S13 iteration (2026-10-21): a name-first claim put "DHH:" on all 23 Positions cards.
        assert not str(_example_item("podcast")["claim"]).startswith("Tegmark")


class TestSingleCallRules:
    def test_should_carry_the_no_invention_line_under_data_accuracy(self) -> None:
        system = _single_call_system()
        accuracy = system[system.index("Data accuracy:") : system.index("Field-level precision:")]

        assert _NO_INVENTION in accuracy

    @pytest.mark.parametrize("retired", _RETIRED)
    def test_should_name_no_retired_component_in_the_map(self, retired: str) -> None:
        assert retired not in _component_map(_single_call_system())

    def test_should_route_key_ideas_to_spot_explorer_and_stat_banner_in_the_map(self) -> None:
        line = next(
            ln for ln in _component_map(_single_call_system()).splitlines() if "keyIdeas[]" in ln
        )

        assert ("→ spot_explorer" in line, "→ stat_banner" in line) == (True, True)

    def test_should_name_only_registered_components_in_the_map(self) -> None:
        named = set(re.findall(r"(?:→|\bor) ([a-z_]+)", _component_map(_single_call_system())))

        assert named - valid_components() - _RENDERER_ONLY == set()
