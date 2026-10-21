"""The split extraction's three cache blocks (pipeline-1min 3.4, Kfir's layout)."""

from __future__ import annotations

import hashlib
from unittest.mock import patch

import pytest

from src.services.pipeline import extraction_group_prompt
from src.services.pipeline.extraction_chunks import ExtractionChunk
from src.services.pipeline.extraction_group_prompt import (
    GroupJob,
    build_group_messages,
    emit_only,
    prefix_block,
    tabs_to_serve,
    with_group_rules,
)
from src.services.pipeline.extraction_prompt import (
    ExtractionPrompt,
    ExtractionPromptInput,
    build_extraction_prompt,
)
from src.services.pipeline.field_groups import ExtractionGroup
from src.services.pipeline.field_requests import FieldKind, FieldReason, FieldRequest
from src.services.pipeline.schema_fields import SchemaParseError

_TABS = [
    {
        "id": "ingredients",
        "label": "Ingredients",
        "component": "checklist",
        "dataSource": "food.ingredients",
    },
    {"id": "steps", "label": "Steps", "component": "step_player", "dataSource": "food.steps"},
    {"id": "tips", "label": "Tips", "component": "tip_callout", "dataSource": "food.tips"},
]
_ANNOTATIONS = "<visual_annotations>\n[0:12] whisking eggs | 3 EGGS\n</visual_annotations>"
_FRAMES = "0:12 — whisking eggs [cooking]"
_MEMORY = "<video_memory>\ndomains: food\n</video_memory>"


def _field(
    field: str,
    *,
    kind: FieldKind = "list",
    cap: int | None = 25,
    tab: str = "",
    domain: str = "food",
    reason: FieldReason = "planned",
) -> FieldRequest:
    tabs = (tab,) if tab else ()
    return FieldRequest(domain, field, kind, reason, tabs, cap, 5, 100, False)


def _group(group_id: str, *fields: FieldRequest, visual: bool = False) -> ExtractionGroup:
    tab_ids = tuple(dict.fromkeys(t for f in fields for t in f.tab_ids))
    return ExtractionGroup(group_id, fields, visual, 500, tab_ids)


_TEXT1 = _group(
    "text1",
    _field("ingredients", cap=30, tab="ingredients"),
    _field("meta", kind="scalar", cap=None),
)
_TEXT2 = _group("text2", _field("steps", tab="steps"))
_VISUAL = _group("visual", _field("tips", cap=10, tab="tips"), visual=True)


def _prompt(tag: str = "food") -> ExtractionPrompt:
    return build_extraction_prompt(
        ExtractionPromptInput(
            [tag],
            [],
            "quality rules text",
            primary_tag=tag,
            title="Lasagna",
            duration_seconds=600,
            tabs=_TABS,
            video_memory=_MEMORY,
        )
    )


def _todays_element(prompt: ExtractionPrompt, tag: str) -> str:
    """The ``<tag …>…</tag>`` element today's single call sends in its job block."""
    job = prompt.user_blocks("[0:00] hi")[1]["text"]
    start, end = job.find(f"<{tag}"), job.find(f"</{tag}>")
    return job[start : end + len(tag) + 3] if start != -1 else ""


def _job() -> GroupJob:
    return GroupJob(_TABS, ["food"], "food", visual_annotations=_ANNOTATIONS, frame_context=_FRAMES)


def _blocks(group: ExtractionGroup, chunk: ExtractionChunk | None = None) -> list:
    return build_group_messages(_prompt(), group, chunk or ExtractionChunk(0, "[0:00] hi"), _job())


# The rule 4.0d round 2 measured (scripts/_model_ab_variants.py) — the group
# calls' own rules since the 4.0 picks; p1.11 appends the argument rule to it.
_COMPLETENESS = (
    "Completeness: emit every distinct step, item or command the transcript supports "
    "— never merge two into one, never skip one."
)
_ARGUMENTS = (
    "Arguments the same way: every distinct claim, its reasoning and the sentence that "
    "carries it — never merge two claims into one, never skip the last 10 minutes (the close "
    "is where a talk lands its ask and a conversation its verdict)."
)
# Kfir 2026-10-21 (D13, S06 iteration): the completeness rule repeats "never invent numbers".
_NUMBERS = (
    "Never invent a number: a duration, rest time, quantity, count or score the speaker does "
    "not state stays null — complete means every item the transcript supports, never a figure "
    "it does not."
)


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


_WORKOUT_TAB = {
    "id": "workout",
    "label": "Workout",
    "component": "workout_room",
    "dataSource": "fitness.exercises",
    "brief": {"what": "every exercise with sets and reps", "expect": 8},
}


def _workout_group(field: str, reason: FieldReason) -> ExtractionGroup:
    request = _field(field, domain="fitness", tab="workout", reason=reason, cap=20)
    return _group("text2", request)


def _workout_job() -> GroupJob:
    return GroupJob([_WORKOUT_TAB], ["fitness"], "fitness")


class TestCacheLayout:
    def test_should_send_block_1_with_ttl_1h_before_block_2_with_5m(self) -> None:
        controls = [block.get("cache_control") for block in _blocks(_TEXT1)]

        assert controls == [{"type": "ephemeral", "ttl": "1h"}, {"type": "ephemeral"}, None]

    def test_should_hash_block_1_and_block_2_identically_across_every_group_of_a_run(self) -> None:
        hashes = {
            tuple(_sha(block["text"]) for block in _blocks(group)[:2])
            for group in (_TEXT1, _TEXT2, _VISUAL)
        }

        assert len(hashes) == 1

    def test_should_send_todays_head_block_as_block_2(self) -> None:
        prompt = _prompt()

        block = build_group_messages(prompt, _TEXT2, ExtractionChunk(0, "[0:00] hi"), _job())[1]

        assert block == prompt.user_blocks("[0:00] hi")[0]

    def test_should_send_todays_rules_schema_and_example_bytes_as_block_1(self) -> None:
        prompt = _prompt()
        expected = "\n\n".join(
            [
                prompt.system,
                _todays_element(prompt, "schema"),
                _todays_element(prompt, "extraction_example"),
            ]
        )

        assert (
            build_group_messages(prompt, _TEXT1, ExtractionChunk(0, "t"), _job())[0]["text"]
            == expected
        )

    def test_should_send_rules_and_schema_only_when_the_primary_has_no_example(self) -> None:
        prompt = _prompt("gaming")
        expected = f"{prompt.system}\n\n{_todays_element(prompt, 'schema')}"

        assert (
            build_group_messages(prompt, _TEXT1, ExtractionChunk(0, "t"), _job())[0]["text"]
            == expected
        )

    def test_should_put_the_rules_schemas_and_example_in_block_1(self) -> None:
        text = _blocks(_TEXT1)[0]["text"]

        assert ("<quality_rules>" in text, "<schema>" in text, "<extraction_example" in text) == (
            True,
            True,
            True,
        )

    def test_should_keep_the_single_calls_job_out_of_block_1(self) -> None:
        assert "Tabs you serve:" not in _blocks(_TEXT1)[0]["text"]

    def test_should_keep_frame_text_out_of_the_cached_blocks(self) -> None:
        cached = "".join(block["text"] for block in _blocks(_VISUAL)[:2])

        assert "3 EGGS" not in cached

    def test_should_refuse_a_template_without_a_schema_element(self) -> None:
        prompt = ExtractionPrompt(
            system="rules", head="<video>{transcript}</video>", tail="<your_job>"
        )

        with pytest.raises(ValueError, match="<schema>"):
            prefix_block(prompt)


class TestGroupRules:
    def test_should_end_the_rules_with_the_completeness_rule(self) -> None:
        system = with_group_rules(_prompt()).system

        assert (
            f"quality rules text\n\n{_COMPLETENESS}\n{_ARGUMENTS}\n{_NUMBERS}\n</quality_rules>"
            in system
        )

    def test_should_change_nothing_but_the_rules_and_the_tails_examples(self) -> None:
        prompt = _prompt()
        ruled = with_group_rules(prompt)

        # p1.11: the tail carries the rules' argument examples for the job block.
        assert (ruled.head, ruled.tail.startswith(prompt.tail.rstrip())) == (prompt.head, True)

    def test_should_keep_the_completeness_rule_out_of_the_single_call(self) -> None:
        assert "Completeness:" not in _prompt().system

    def test_should_keep_todays_rules_when_the_group_rules_are_empty(self) -> None:
        prompt = _prompt()
        with patch.object(extraction_group_prompt, "load_prompt_text", return_value="\n"):
            assert with_group_rules(prompt) == prompt

    def test_should_keep_todays_rules_without_a_single_closing_tag(self) -> None:
        prompt = ExtractionPrompt(system="rules", head="<video>", tail="<schema>s</schema>")

        assert with_group_rules(prompt) == prompt


class TestJobBlock:
    def test_should_emit_only_the_groups_keys_with_their_caps(self) -> None:
        assert emit_only(_TEXT1).splitlines()[0] == (
            "Emit ONLY these keys (anything else is discarded): food.ingredients (cap 30), food.meta"
        )

    def test_should_show_the_groups_output_shape(self) -> None:
        assert emit_only(_TEXT2).splitlines()[1] == 'Output shape: {"food":{"steps":[…]}}'

    def test_should_list_only_the_tabs_the_group_serves(self) -> None:
        job = _blocks(_TEXT2)[2]["text"]

        assert ("Steps — step_player" in job, "Ingredients — checklist" in job) == (True, False)

    def test_should_leave_frame_text_out_of_a_text_group(self) -> None:
        job = _blocks(_TEXT1)[2]["text"]

        assert ("<visual_context_guide>" in job, "3 EGGS" in job, "<key_frames>" in job) == (
            False,
            False,
            False,
        )

    def test_should_give_the_visual_group_the_annotations_and_key_frames(self) -> None:
        job = _blocks(_VISUAL)[2]["text"]

        assert ("<visual_context_guide>" in job, "3 EGGS" in job, _FRAMES in job) == (
            True,
            True,
            True,
        )

    def test_should_slice_the_annotations_to_the_chunks_window(self) -> None:
        chunk = ExtractionChunk(1, "[0:30] later", start_s=20.0, end_s=None)

        job = _blocks(_VISUAL, chunk)[2]["text"]

        assert "3 EGGS" not in job

    def test_should_carry_the_chunks_batch_context(self) -> None:
        chunk = ExtractionChunk(
            0, "[0:00] hi", batch_context="<batch_partial_context>1 of 2</batch_partial_context>\n"
        )

        assert "1 of 2" in _blocks(_TEXT2, chunk)[2]["text"]

    def test_should_name_a_key_its_schema_lacks_on_a_plain_line(self) -> None:
        group = _group("text1", _field("steps", tab="steps"), _field("notAKey", tab="steps"))

        assert emit_only(group).splitlines()[2] == (
            "Also emit (shapes as in <schema>): food.notAKey (cap 25)"
        )

    def test_should_never_emit_nothing_for_a_domain_without_a_schema(self) -> None:
        group = _group("text1", _field("items", domain="nodomain", tab="steps"))

        assert emit_only(group) == (
            "Emit ONLY these keys (anything else is discarded): nodomain.items (cap 25)"
        )

    def test_should_give_the_full_brief_to_the_group_extracting_the_tabs_own_field(self) -> None:
        group, job = _workout_group("exercises", "planned"), _workout_job()

        assert "expect ~8" in tabs_to_serve(group, job)

    def test_should_name_a_tab_it_only_backs_up_without_its_brief(self) -> None:
        group, job = _workout_group("timer", "upgrade"), _workout_job()

        assert tabs_to_serve(group, job) == "- supports: Workout (workout_room)"

    def test_should_name_the_keys_of_a_domain_whose_schema_does_not_parse(self) -> None:
        with patch.object(
            extraction_group_prompt, "slice_skeleton", side_effect=SchemaParseError("broken")
        ):
            line = emit_only(_TEXT2)

        assert line == "Emit ONLY these keys (anything else is discarded): food.steps (cap 25)"
