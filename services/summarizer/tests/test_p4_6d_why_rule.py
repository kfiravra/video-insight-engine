"""p4.6d — every key idea carries its why (output-quality, g4 G4-6).

In five of six gate-4 videos the most valuable missed line was the mechanism. The
group calls' block 1 gains the why rule right after the argument rule: the reason or
mechanism the speaker gives for the claim, in the speaker's own terms — never the
claim reworded, never a reason of the model's — and the marker "Stated without a
reason" when the speaker gives none. ``schemas/learning.txt``'s ``why`` line says the
same in short, so the single call (``EXTRACTION_PARALLEL=false``) reads it too. The
talk example's why is the evidence the speaker gave three minutes before the claim;
it used to repeat the claim, and S01's stored idea at 40:40 copied that.
"""

from __future__ import annotations

import json
import re

from src.services.pipeline.extraction_group_prompt import (
    GROUP_RULES_PATH,
    argument_examples,
    completeness_rule,
    prefix_block,
    with_group_rules,
)
from src.services.pipeline.extraction_prompt import (
    ExtractionPrompt,
    ExtractionPromptInput,
    build_extraction_prompt,
)
from src.services.pipeline.prompt_builder import load_prompt_text

_WHY_RULE = (
    "Every key idea carries its why: the reason or mechanism the speaker gives for the claim "
    "(how it works, what causes it, the evidence they cite), in the speaker's own terms and "
    "often said a minute before or after the claim's line — never the claim again in other "
    "words, never a reason of your own; when the speaker gives none, the why is "
    '"Stated without a reason".'
)
_SCHEMA_WHY = (
    '"why": "The reason or mechanism the speaker gives for it, 1-2 sentences with the names '
    'and numbers used — never the claim reworded; \\"Stated without a reason\\" when they '
    'give none",'
)
_MARKER = '"Stated without a reason"'
_TALK_OPEN = '<argument_example field="learning.keyIdeas" primary="learning">'
_STAMP = re.compile(r"\[(\d+):(\d{2})\]")
_IDEAS_TAB = {
    "id": "ideas",
    "label": "Key ideas",
    "component": "spot_explorer",
    "dataSource": "learning.keyIdeas",
}


def _prompt() -> ExtractionPrompt:
    return build_extraction_prompt(
        ExtractionPromptInput(
            ["learning"],
            [],
            "quality rules text",
            title="T",
            duration_seconds=3787,
            tabs=[_IDEAS_TAB],
        )
    )


def _block_1() -> str:
    return str(prefix_block(with_group_rules(_prompt()))["text"])


def _talk_example() -> tuple[str, dict[str, object]]:
    """The talk example's transcript line and its key-idea item."""
    examples = argument_examples(load_prompt_text(GROUP_RULES_PATH))
    body = examples[examples.index(_TALK_OPEN) : examples.index("</argument_example>")]
    lines = body.splitlines()
    item = next(line for line in lines if line.startswith('{"claim"'))
    return next(line for line in lines if "The transcript reads:" in line), json.loads(item)


def _long_words(text: str) -> set[str]:
    return {word for word in re.findall(r"[a-z]+", text.lower()) if len(word) >= 5}


class TestWhyRule:
    def test_should_give_every_group_call_the_why_rule_when_the_run_is_split(self) -> None:
        assert _WHY_RULE in _block_1()

    def test_should_follow_the_argument_rule_when_block_1_is_ruled(self) -> None:
        system = with_group_rules(_prompt()).system
        order = [system.index(s) for s in ("Arguments the same way", _WHY_RULE, "Never invent")]

        assert order == sorted(order)

    def test_should_keep_the_why_rule_out_of_the_single_call(self) -> None:
        prompt = _prompt()

        assert "Every key idea carries its why" not in prompt.system + prompt.tail

    def test_should_name_the_marker_when_the_speaker_gives_no_reason(self) -> None:
        assert _MARKER in completeness_rule(load_prompt_text(GROUP_RULES_PATH))


class TestWhySchemaLine:
    def test_should_define_the_why_in_the_single_calls_schema(self) -> None:
        assert _SCHEMA_WHY in _prompt().tail

    def test_should_define_the_why_in_block_1_when_the_run_is_split(self) -> None:
        assert _SCHEMA_WHY in _block_1()


class TestTalkExampleWhy:
    def test_should_take_the_whys_figures_from_the_example_transcript(self) -> None:
        transcript, item = _talk_example()
        figures = re.findall(r"\d[\d,]*", str(item["why"]))

        assert figures and all(figure in transcript for figure in figures)

    def test_should_share_no_content_word_with_the_claim(self) -> None:
        _, item = _talk_example()

        assert _long_words(str(item["why"])) & _long_words(str(item["claim"])) == set()

    def test_should_say_the_reason_at_least_a_minute_before_the_claim(self) -> None:
        transcript, item = _talk_example()
        minutes, seconds = _STAMP.findall(transcript)[0]

        assert int(str(item["timestamp"])) - (int(minutes) * 60 + int(seconds)) >= 60
