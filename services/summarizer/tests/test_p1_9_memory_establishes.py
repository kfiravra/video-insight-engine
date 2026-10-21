"""p1.9 (D5, R1, R7 — L1, L3): memory states the thesis, stamps its takeaways and
says what each outline section establishes; the provisional Moments tab shows it.

The prompt asks for a claim, not a topic; every takeaway ends with the
``[m:ss]`` it is said at; each outline section carries one short
``establishes`` line, parsed into ``OutlineSection.establishes`` and shipped
as the description of the provisional moment (and of the complete-time
fallback moment). A memory output from before the line still parses, and the
tab then ships the title alone, as before.
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from src.models.memory_types import MemoryInput, MemoryResult, OutlineSection
from src.services.pipeline import memory as memory_mod
from src.services.pipeline.assembly import assemble_response
from src.services.pipeline.assembly.moment_fallback import provisional_moments_tab
from src.services.pipeline.memory import (
    build_memory_prompt,
    parse_memory_output,
    repair_outline,
    repair_takeaways,
)
from src.services.pipeline.phases import moments
from src.services.pipeline.tab_ledger import TabLedger
from src.shared_config.domain_config import effective_requirements

PROMPT_FILE = Path(__file__).parent.parent / "src" / "prompts" / "memory.txt"
DURATION = 1182  # 19:42
EXAMPLE_DURATION = 19 * 60  # the prompt's own example video

_LINES = [
    "the Brownie ended a craft and created new art",
    "chiles carry the flavour; chuck is the cut",
    "a bare simmer keeps the chuck tender",
    "the dipped tortilla is what crisps",
]


def _section(start: str, end: str, title: str, establishes: object = None) -> dict[str, object]:
    section: dict[str, object] = {"start": start, "end": end, "title": title}
    if establishes is not None:
        section["establishes"] = establishes
    return section


def _outline(lines: Sequence[object] | None = _LINES) -> list[dict[str, object]]:
    bounds = [("0:00", "1:10"), ("1:10", "2:40"), ("2:40", "13:00"), ("13:00", "19:42")]
    titles = ["intro", "ingredients", "cooking", "plating"]
    return [
        _section(start, end, title, None if lines is None else lines[i])
        for i, ((start, end), title) in enumerate(zip(bounds, titles, strict=True))
    ]


def _response(**overrides: object) -> dict[str, object]:
    body: dict[str, object] = {
        "outline": _outline(),
        "evidence": {"has_steps": True},
        "tldr": "Birria works because the beef braises in its own consommé",
        "takeaways": [
            "Toast 6 chiles 30 s [3:05]",
            "Braise 3.5 hours [6:40]",
            "Griddle at 400°F [14:20]",
        ],
    }
    body.update(overrides)
    return body


def _lines(outline: list[OutlineSection]) -> list[str]:
    return [s.establishes for s in outline]


def _prompt_example() -> dict[str, Any]:
    """The JSON example the prompt file ships, as the model would return it."""
    text = PROMPT_FILE.read_text()
    start = text.index("{", text.index("Example for"))
    end = text.rindex("}", 0, text.index("</output_format>"))
    return json.loads(text[start : end + 1])


def _block(prompt: str, tag: str) -> str:
    return prompt.split(f"<{tag}>")[1].split(f"</{tag}>")[0]


def _rendered() -> str:
    return build_memory_prompt(
        MemoryInput(title="t", channel="c", duration=DURATION, transcript="[0:00] hi")
    )


# ─── The prompt (render test) ───


class TestMemoryPromptAsksForTheThesis:
    def test_should_ask_for_what_the_video_claims_not_its_topic(self) -> None:
        rules = _block(_rendered(), "summary_rules")

        assert "a position a reader could disagree with" in rules

    def test_should_no_longer_demand_a_number_in_every_takeaway(self) -> None:
        assert "Each one carries a specific number" not in _rendered()

    def test_should_ask_for_the_stamp_on_every_takeaway(self) -> None:
        rules = _block(_rendered(), "summary_rules")

        assert "[m:ss]" in rules

    def test_should_ask_one_establishes_line_per_section(self) -> None:
        rules = _block(_rendered(), "outline_rules")

        assert '"establishes"' in rules

    def test_should_ship_an_example_the_parser_reads_whole(self) -> None:
        result = parse_memory_output(_prompt_example(), EXAMPLE_DURATION)

        assert result is not None and [bool(s.establishes) for s in result.outline] == [True] * 4

    def test_should_stamp_every_takeaway_in_its_example(self) -> None:
        takeaways = _prompt_example()["takeaways"]

        assert [memory_mod._TRAILING_STAMP_RE.search(t) is not None for t in takeaways] == [
            True
        ] * memory_mod.takeaway_count(EXAMPLE_DURATION)

    def test_should_keep_its_example_tldr_within_the_cap(self) -> None:
        assert len(_prompt_example()["tldr"]) <= memory_mod.TLDR_MAX_CHARS


# ─── Parsing the establishes line ───


class TestMemoryPromptForbidsInventedNumbers:
    # S06 iteration (2026-10-21): the memory hero said "rest 10 minutes" for "give it a sec".
    def test_should_forbid_a_duration_the_speaker_did_not_say(self) -> None:
        assert "Never state a duration, quantity or number the speaker did not say" in (
            PROMPT_FILE.read_text()
        )

    def test_should_forbid_moving_a_time_from_one_step_to_another(self) -> None:
        assert "never moves to another" in PROMPT_FILE.read_text()


class TestEstablishesParsing:
    def test_should_keep_each_sections_establishes_line(self) -> None:
        outline = repair_outline(_outline(), DURATION)

        assert _lines(outline) == _LINES

    def test_should_parse_an_outline_from_before_the_line(self) -> None:
        outline = repair_outline(_outline(lines=None), DURATION)

        assert (len(outline), _lines(outline)) == (4, [""] * 4)

    def test_should_ignore_a_malformed_line_and_keep_the_section(self) -> None:
        outline = repair_outline(_outline(lines=[42, None, ["x"], {"a": 1}]), DURATION)

        assert (len(outline), _lines(outline)) == (4, [""] * 4)

    def test_should_cut_a_long_line_at_a_word_boundary(self) -> None:
        outline = repair_outline(_outline(lines=["word " * 60, "b", "c", "d"]), DURATION)

        line = outline[0].establishes
        assert len(line) <= memory_mod.ESTABLISHES_MAX_CHARS and line.endswith("word…")

    def test_should_collapse_whitespace_in_the_line(self) -> None:
        outline = repair_outline(_outline(lines=["  a\n  b  ", "b", "c", "d"]), DURATION)

        assert outline[0].establishes == "a b"

    def test_should_keep_the_line_when_boundaries_are_repaired(self) -> None:
        raw = [
            _section("0:00", "1:30", "intro", _LINES[0]),  # overlaps the next start
            _section("1:10", "2:00", "ingredients", _LINES[1]),  # gap until the next start
            _section("2:40", "13:00", "cooking", _LINES[2]),
            _section("13:00", "19:42", "plating", _LINES[3]),
        ]

        outline = repair_outline(raw, DURATION)

        assert _lines(outline) == _LINES

    def test_should_keep_the_line_of_the_section_that_absorbs_a_short_one(self) -> None:
        raw = [
            *_outline()[:3],
            _section("13:00", "13:30", "tiny", "a line that folds away"),
            _section("13:30", "19:42", "plating", _LINES[3]),
        ]

        outline = repair_outline(raw, DURATION)

        assert _lines(outline)[2:] == [_LINES[2], _LINES[3]]

    def test_should_carry_the_line_into_the_video_meta_shape(self) -> None:
        section = OutlineSection(start=0, end=70, title="intro", establishes=_LINES[0])

        assert section.model_dump()["establishes"] == _LINES[0]


# ─── Takeaway stamps ───


class TestTakeawayStamps:
    def test_should_keep_a_closing_stamp_inside_the_video(self) -> None:
        takeaways = repair_takeaways(["A claim [3:05]", "B [6:40]", "C [14:20]"], DURATION)

        assert takeaways == ["A claim [3:05]", "B [6:40]", "C [14:20]"]

    def test_should_cut_a_closing_stamp_past_the_end_but_keep_the_claim(self) -> None:
        takeaways = repair_takeaways(["A claim [25:00]", "B [6:40]", "C [14:20]"], DURATION)

        assert takeaways[0] == "A claim"

    def test_should_keep_a_takeaway_without_a_stamp(self) -> None:
        takeaways = repair_takeaways(["A claim", "B [6:40]", "C [14:20]"], DURATION)

        assert takeaways[0] == "A claim"

    def test_should_read_an_hour_clock_stamp_in_a_long_video(self) -> None:
        takeaways = repair_takeaways(["A [1:02:05]", "B [1:00:00]", "C [0:10]"], 4200)

        assert takeaways == ["A [1:02:05]", "B [1:00:00]", "C [0:10]"]

    def test_should_leave_stamps_alone_without_a_duration(self) -> None:
        """Synthesis repairs its own takeaways without a duration — nothing is cut."""
        takeaways = repair_takeaways(["A [99:00]", "B", "C"])

        assert takeaways[0] == "A [99:00]"

    def test_should_range_check_the_stamps_when_parsing_the_memory_output(self) -> None:
        raw = _response(takeaways=["A [25:00]", "B [6:40]", "C [14:20]"])

        result = parse_memory_output(raw, DURATION)

        assert result is not None and result.takeaways[0] == "A"


# ─── The provisional Moments tab carries the line ───

_META = {"duration": DURATION, "chapters": [], "outline": [
    {"start": 0, "end": 70, "title": "intro", "establishes": _LINES[0]},
    {"start": 70, "end": 160, "title": "ingredients", "establishes": _LINES[1]},
]}  # fmt: skip
_OLD_META = {"duration": DURATION, "chapters": [], "outline": [
    {"start": 0, "end": 70, "title": "intro"},
    {"start": 70, "end": 160, "title": "ingredients"},
]}  # fmt: skip
_CODE_TAB = {"id": "code", "label": "Code", "component": "code_playground",
             "dataSource": "tech.snippets", "goal": "Run it"}  # fmt: skip
_MOMENTS_TAB = {"id": "moments", "label": "🎬 Demos", "emoji": "🎬", "component": "moment_track",
                "dataSource": "learning.timestamps", "goal": "Jump to a demo"}  # fmt: skip
_STEPS_TAB = {"id": "steps", "label": "Steps", "component": "step_player"}
_TECH = effective_requirements("tech")
_MEMORY = MemoryResult(
    outline=[
        OutlineSection(start=0, end=70, title="intro", establishes=_LINES[0]),
        OutlineSection(start=70, end=160, title="ingredients", establishes=_LINES[1]),
    ]
)


def _descriptions(built: tuple[dict, int] | None) -> list[str | None]:
    tab, _position = built or ({"props": {"items": []}}, 0)
    return [m.get("description") for m in tab["props"]["items"]]


def _ctx(tabs: list[dict], **overrides: Any) -> SimpleNamespace:
    fields: dict[str, Any] = {
        "video_summary_id": "vsid",
        "video_data": SimpleNamespace(title="t", channel="c", duration=DURATION, chapters=None),
        "triage": SimpleNamespace(primary_tag="tech", tabs=tabs),
        "content_format": None,
        "memory": _MEMORY,
        "description_analysis": None,
        "tab_ledger": TabLedger(),
    }
    return SimpleNamespace(**{**fields, **overrides})


def _payload(chunk: str | None) -> dict:
    assert chunk is not None
    return json.loads(chunk.removeprefix("data: "))


class TestProvisionalMomentsCarryTheLine:
    def test_should_describe_each_moment_with_what_its_section_establishes(self) -> None:
        built = provisional_moments_tab([_CODE_TAB, _MOMENTS_TAB], _META, None, "tech", _TECH)

        assert _descriptions(built) == _LINES[:2]

    def test_should_ship_the_title_alone_for_an_outline_from_before_the_line(self) -> None:
        built = provisional_moments_tab([_CODE_TAB, _MOMENTS_TAB], _OLD_META, None, "tech", _TECH)

        assert _descriptions(built) == [None, None]

    def test_should_drop_a_line_that_only_repeats_the_title(self) -> None:
        meta = {**_META, "outline": [{**_META["outline"][0], "establishes": "intro"}]}

        built = provisional_moments_tab([_MOMENTS_TAB], meta, None, "tech", _TECH)

        assert _descriptions(built) == [None]

    def test_should_preview_nothing_when_the_steps_are_the_timeline(self) -> None:
        """D6 holds: a recipe navigated by its steps gets no Moments tab, lines or not."""
        built = provisional_moments_tab(
            [_STEPS_TAB, _MOMENTS_TAB], _META, None, "food", effective_requirements("food")
        )

        assert built is None

    def test_should_stream_the_line_in_the_plan_done_tab_ready(self) -> None:
        payload = _payload(moments.provisional_moments_event(_ctx([_MOMENTS_TAB])))  # type: ignore[arg-type]

        assert [m.get("description") for m in payload["props"]["items"]] == _LINES[:2]

    def test_should_stream_it_as_provisional_for_the_web_to_mark_preview(self) -> None:
        payload = _payload(moments.provisional_moments_event(_ctx([_MOMENTS_TAB])))  # type: ignore[arg-type]

        assert (payload["provisional"], payload["final"]) == (True, False)

    def test_should_let_the_real_tab_replace_the_preview_by_id_at_complete(self) -> None:
        ctx = _ctx([_MOMENTS_TAB])
        moments.provisional_moments_event(ctx)  # type: ignore[arg-type]
        real = {**_MOMENTS_TAB, "props": {"items": [{"seconds": 5, "label": "Real"}]}}

        payload = _payload(ctx.tab_ledger.event(real, 1))

        assert (payload.get("provisional"), ctx.tab_ledger.provisional) == (None, None)

    def test_should_describe_the_complete_time_fallback_tab_too(self) -> None:
        """Assembly's always-Moments tab reads the same outline: the line ships there as well."""
        triage = {"contentTags": ["tech"], "primaryTag": "tech", "tabs": [_CODE_TAB]}
        snippet = {"filename": "x.py", "language": "python", "code": "x = 1", "explanation": "demo"}

        result = assemble_response(
            triage, {"tech": {"snippets": [snippet, snippet]}}, None, None, video_meta=_META
        )

        tab = next(t for t in result["tabs"] if t["component"] == "moment_track")
        assert [m.get("description") for m in tab["props"]["items"]] == _LINES[:2]
