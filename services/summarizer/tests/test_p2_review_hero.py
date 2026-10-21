"""Phase-2 review fixes to the hero rewrite (output-quality; review p2 B1–B6, C1, D3–D5).

B1 a tab is named only in the label's own case or in a sentence about tabs, and the
retry hears which label · B2 a quote the transcript never says is out of what the
hero reads · B3 / D4 without the transcript a takeaway cites an id, or types a clock
near one the call read · B4 what finalize repairs costs no retry · B5 a verdict
needs a person · B6 "Dr." / "e.g." / "J." end no sentence · C1 quoted spans are
checked against the transcript · D3 / D5 the prompt's examples and brief rule.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest

from src.services.pipeline import hero_rewrite
from src.services.pipeline.hero_compact import compact_for_hero
from src.services.pipeline.hero_job import GroupBlocks, HeroRequest
from src.services.pipeline.hero_rewrite import run_hero_rewrite
from src.services.pipeline.hero_validate import (
    HeroDraft,
    HeroEvidence,
    Problem,
    check_hero,
    finalize,
    parse_draft,
)
from src.services.pipeline.quote_verify import QuoteIndex
from tests.test_p2_1_hero_rewrite import _CHUNK, _CHUNKS, _EXTRACTION, _prompt
from tests.test_p2_1_hero_rewrite import _GOOD as _ANSWER

_TABS = ("💡 Key Ideas", "🗓️ Day by Day", "🧠 Test Yourself", "🎬 Moments")
_EVIDENCE_TEXT = (
    "Alice on agents. Dr. Ramos and J. Smith argue that 37signals went pencils down in "
    "2025, e.g. Rails 8 vs. Go, and wrote 150,000 lines. The Kodak Brownie made painters pivot."
)
_BRIEF = (
    "Alice opens on the Kodak Brownie. She argues that 37signals went pencils down in 2025. "
    "Her output grew to 150,000 lines. The close asks for total optimism."
)
_GOOD: dict[str, Any] = {
    "tldr": "Hand-written code stopped paying in 2025, so let agents write it.",
    "brief": _BRIEF,
    "keyTakeaways": [
        {"text": "Hand-written code no longer pays.", "ref": "K1"},
        {"text": "Ship a CLI by next Friday.", "ref": "K2"},
        {"text": "Output grew to 150,000 lines.", "ref": "M1"},
    ],
    "worthItFor": "Engineering leads deciding how far to let agents write their code.",
    "skipIf": "You want a how-to: no code is written on stage.",
    "seoDescription": "Alice on why hand-written code stopped paying.",
}
# Said at 60 s: what a quote may copy.
_SAID = "Hand-written code no longer pays its way, she says."
_UNSAID = "agents write all the code now"


def _index(language: str | None = None) -> QuoteIndex:
    segments = [
        {"text": "We open on the Kodak Brownie.", "start": 0},
        {"text": _SAID, "start": 60},
    ]
    return QuoteIndex.build(segments, language)


def _evidence(**changes: Any) -> HeroEvidence:
    values: dict[str, Any] = {
        "text": _EVIDENCE_TEXT,
        "stamps": {"K1": 60, "K2": 245, "M1": 400},
        "duration": 600,
        "tab_labels": _TABS,
        "takeaway_count": 3,
    }
    return HeroEvidence(**{**values, **changes})


def _checked(
    evidence: HeroEvidence | None = None, /, **changes: Any
) -> tuple[HeroDraft, list[Problem]]:
    evidence = evidence or _evidence()
    draft = parse_draft({**_GOOD, **changes}, evidence)
    assert draft is not None
    return draft, check_hero(draft, evidence)


def _problems(evidence: HeroEvidence | None = None, /, **changes: Any) -> list[Problem]:
    return _checked(evidence, **changes)[1]


def _fields(problems: list[Problem]) -> list[str]:
    return [p.field for p in problems]


# ─── B1: tab names ───


class TestTabNames:
    @pytest.mark.parametrize(
        ("field", "text"),
        [
            ("brief", f"{_BRIEF} The talk's key ideas build to one ask."),
            ("tldr", "It walks the case for agents day by day, so hand-written code stops."),
            ("worthItFor", "Leads who want to test yourself drills on agents."),
            ("skipIf", "You want the key moments of a demo: no code runs on stage."),
        ],
    )
    def test_should_pass_prose_sharing_words_with_a_label_when_not_in_its_own_case(
        self, field: str, text: str
    ) -> None:
        assert _problems(**{field: text}) == []

    @pytest.mark.parametrize(
        "sentence",
        ["The plan runs as a Day by Day list.", "Key Ideas hold every argument."],
    )
    def test_should_fail_a_sentence_writing_a_label_in_its_own_case(self, sentence: str) -> None:
        assert [(p.field, p.index) for p in _problems(brief=f"{_BRIEF} {sentence}")] == [
            ("brief", 4)
        ]

    def test_should_fail_a_label_in_any_case_when_the_sentence_speaks_of_tabs(self) -> None:
        problems = _problems(brief=f"{_BRIEF} The key ideas section lists every argument.")

        assert [(p.field, p.index) for p in problems] == [("brief", 4)]

    def test_should_pass_a_one_word_label_opening_the_sentence(self) -> None:
        assert _problems(brief=f"{_BRIEF} Moments later she closes on optimism.") == []

    def test_should_name_the_matched_label_when_a_sentence_names_a_tab(self) -> None:
        problems = _problems(brief=f"{_BRIEF} The plan runs as a Day by Day list.")

        assert [p.message for p in problems] == [
            'brief sentence 5 names the tab "Day by Day" — describe the video, never the page'
        ]


# ─── B5: creator judgement ───


class TestCreatorJudgement:
    @pytest.mark.parametrize(
        "line",
        [
            "Readers who see the speaker is arguing, not building, the case.",
            "Founders who want to know what the host is betting on.",
            "You want strong delivery tips for your own talks.",
        ],
    )
    def test_should_pass_a_neutral_line_when_no_verdict_judges_a_person(self, line: str) -> None:
        assert _problems(worthItFor=line) == []

    @pytest.mark.parametrize(
        "line",
        [
            "The speaker is a bit boring.",
            "You think his delivery is dull.",
            "You came for her strong delivery.",
            "The host comes across as tedious.",
        ],
    )
    def test_should_fail_a_line_when_a_verdict_judges_the_creator(self, line: str) -> None:
        assert _fields(_problems(skipIf=line)) == ["skipIf"]


# ─── B6: sentence breaks ───

_ABBREVIATED_BRIEF = (
    "Dr. Ramos opens on the Kodak Brownie. Mr. Smith answers with Rails, e.g. Rails 8 vs. Go. "
    "J. Smith says 37signals went pencils down in 2025. Output grew to 150,000 lines. "
    "The close asks for total optimism."
)


class TestSentenceBreaks:
    def test_should_count_five_sentences_when_titles_and_short_forms_end_none(self) -> None:
        draft, problems = _checked(brief=_ABBREVIATED_BRIEF)

        assert (len(draft.sentences), problems) == (5, [])

    def test_should_keep_the_closing_sentence_when_the_brief_names_a_doctor(self) -> None:
        draft, problems = _checked(brief=_ABBREVIATED_BRIEF)

        result = finalize(draft, problems, 3)

        assert result is not None and result.brief.endswith("The close asks for total optimism.")


# ─── C1: quoted spans ───


class TestQuotedSpans:
    def test_should_drop_a_brief_sentence_when_its_quote_is_never_said(self) -> None:
        evidence = _evidence(quote_index=_index())
        draft, problems = _checked(evidence, brief=f'{_BRIEF} She says "{_UNSAID}".')

        result = finalize(draft, problems, 3)

        assert [(p.field, p.repair, p.index) for p in problems] == [("brief", "drop_sentence", 4)]
        assert result is not None and result.brief == _BRIEF

    def test_should_keep_the_marks_when_the_quote_is_said(self) -> None:
        sentence = 'She says "hand-written code no longer pays its way".'
        draft, problems = _checked(_evidence(quote_index=_index()), brief=f"{_BRIEF} {sentence}")

        assert (problems, draft.sentences[-1]) == ([], sentence)

    def test_should_drop_every_sentence_a_never_said_quote_runs_across(self) -> None:
        brief = (
            ". ".join(_BRIEF.split(". ")[:3]) + '. She ends: "Stop typing. Agents write it all."'
        )
        problems = _problems(_evidence(quote_index=_index()), brief=brief)

        assert [p.index for p in problems] == [3, 4]

    @pytest.mark.parametrize(
        ("field", "value"),
        [
            ("tldr", f'Alice says "{_UNSAID}", so stop typing.'),
            ("keyTakeaways", [*_GOOD["keyTakeaways"][:2], {"text": f'"{_UNSAID}".', "ref": "M1"}]),
            ("worthItFor", f'Leads who heard "{_UNSAID}" and doubt it.'),
            ("skipIf", f'You want proof that "{_UNSAID}" holds.'),
        ],
    )
    def test_should_fail_the_field_when_it_quotes_words_never_said(
        self, field: str, value: Any
    ) -> None:
        problems = _problems(_evidence(quote_index=_index()), **{field: value})

        assert _fields(problems) == [field] and "never says" in problems[0].message

    def test_should_not_check_a_quoted_span_under_three_words(self) -> None:
        brief = f'{_BRIEF} Her "pencils down" call opens it.'

        assert _problems(_evidence(quote_index=_index()), brief=brief) == []

    def test_should_strip_the_marks_when_a_non_english_source_has_the_quote_only_translated(
        self,
    ) -> None:
        evidence = _evidence(quote_index=_index("he"))

        draft, problems = _checked(evidence, tldr=f'Alice says "{_UNSAID}", so stop typing.')

        assert (problems, draft.tldr) == ([], f"Alice says {_UNSAID}, so stop typing.")


# ─── The call: fake LLM ───


@pytest.fixture
def fake_llm() -> Iterator[AsyncMock]:
    """``call_llm_with_retry`` faked: replies in order, then the good answer."""
    replies: list[str | None] = []

    async def answer(*_args: Any, **_kwargs: Any) -> str | None:
        return replies.pop(0) if replies else json.dumps(_ANSWER)

    mock = AsyncMock(side_effect=answer)
    mock.replies = replies  # type: ignore[attr-defined]
    with patch.object(hero_rewrite, "call_llm_with_retry", mock):
        yield mock


def _reply(**changes: Any) -> str:
    return json.dumps({**_ANSWER, **changes})


def _job(mock: AsyncMock, call: int = 0) -> str:
    return mock.await_args_list[call].args[1][-1]["text"]


_MEMORY_WITH_CLOCKS = "<video_memory>\noutline:\n  2:00–3:30 The middle\n</video_memory>"


def _request(
    *,
    chunked: bool = False,
    memory: str = "",
    extraction: dict[str, Any] = _EXTRACTION,
    quote_index: QuoteIndex | None = None,
) -> HeroRequest:
    return HeroRequest(
        title="Alice on agents",
        duration=600,
        extraction=extraction,
        tab_labels=["💡 Key Ideas", "🎬 Moments"],
        video_memory=memory,
        blocks=GroupBlocks(_prompt(), _CHUNKS if chunked else (_CHUNK,)),
        quote_index=quote_index,
    )


def _typed(stamp: str) -> list[dict[str, str]]:
    return [*_ANSWER["keyTakeaways"][:2], {"text": "Optimism is the play.", "timestamp": stamp}]


# ─── B3 / D4: stamps without the transcript ───


class TestStampsWithoutTranscript:
    async def test_should_ask_for_a_ref_on_every_takeaway_when_no_transcript_is_read(
        self, fake_llm: AsyncMock
    ) -> None:
        await run_hero_rewrite(object(), _request(chunked=True))  # type: ignore[arg-type]

        job = _job(fake_llm)
        assert ('Always cite a "ref"' in job, '"timestamp": "m:ss"' in job) == (True, False)

    async def test_should_offer_a_typed_clock_when_the_transcript_is_read(
        self, fake_llm: AsyncMock
    ) -> None:
        await run_hero_rewrite(object(), _request())  # type: ignore[arg-type]

        assert '"timestamp": "m:ss"' in _job(fake_llm)

    async def test_should_fail_a_typed_clock_far_from_every_clock_read_when_no_transcript(
        self, fake_llm: AsyncMock
    ) -> None:
        fake_llm.replies.extend([_reply(keyTakeaways=_typed("6:00"))] * 2)  # type: ignore[attr-defined]

        result = await run_hero_rewrite(object(), _request(chunked=True))  # type: ignore[arg-type]

        assert (result, "no transcript was sent" in _job(fake_llm, 1)) == (None, True)

    @pytest.mark.parametrize(
        ("stamp", "memory", "seconds"),
        [("7:00", "", 420), ("3:20", _MEMORY_WITH_CLOCKS, 200)],
    )
    async def test_should_accept_a_typed_clock_within_30_s_of_a_clock_the_call_read(
        self, fake_llm: AsyncMock, stamp: str, memory: str, seconds: int
    ) -> None:
        fake_llm.replies.append(_reply(keyTakeaways=_typed(stamp)))  # type: ignore[attr-defined]

        result = await run_hero_rewrite(object(), _request(chunked=True, memory=memory))  # type: ignore[arg-type]

        assert result is not None and result.key_takeaways[2].timestamp == seconds


# ─── B4: retry only where it can help ───


class TestRetryPolicy:
    @pytest.mark.parametrize(
        "changes",
        [
            {"keyTakeaways": [*_ANSWER["keyTakeaways"], {"text": "Optimism wins.", "ref": "M2"}]},
            {"brief": f"{_ANSWER['brief']} It is optimistic. It ends on optimism."},
            {"brief": f"{_ANSWER['brief']} It began in 1946."},
            {"seoDescription": "Alice in 1999 on why hand-written code stopped paying."},
        ],
    )
    async def test_should_make_one_call_when_finalize_repairs_the_answer(
        self, fake_llm: AsyncMock, changes: dict[str, Any]
    ) -> None:
        fake_llm.replies.append(_reply(**changes))  # type: ignore[attr-defined]

        result = await run_hero_rewrite(object(), _request())  # type: ignore[arg-type]

        assert (result is not None, fake_llm.await_count) == (True, 1)

    async def test_should_retry_when_finalize_would_ship_a_d18_line_empty(
        self, fake_llm: AsyncMock
    ) -> None:
        fake_llm.replies.append(_reply(skipIf="You want the 3 hour cut."))  # type: ignore[attr-defined]

        result = await run_hero_rewrite(object(), _request())  # type: ignore[arg-type]

        assert fake_llm.await_count == 2
        assert result is not None and result.skip_if == _ANSWER["skipIf"]


# ─── B2: quotes the transcript never says ───

_INVENTED_QUOTE = "we shipped 9,000 apps by hand last year"
_INVENTED: dict[str, Any] = {
    "learning": {
        **_EXTRACTION["learning"],
        "keyIdeas": [
            {
                "claim": "Hand-written code no longer pays",
                "quote": _INVENTED_QUOTE,
                "timestamp": 60,
            },
            *_EXTRACTION["learning"]["keyIdeas"][1:],
        ],
    }
}


class TestUnsaidQuotesOutOfTheEvidence:
    def test_should_leave_a_never_said_quote_out_of_the_lines_and_the_quotes(self) -> None:
        compact = compact_for_hero(_INVENTED, 600, quote_index=_index())

        assert ("9,000" in compact.text, "K1" in compact.quotes) == (False, False)

    def test_should_keep_a_quote_a_non_english_source_has_only_translated(self) -> None:
        compact = compact_for_hero(_INVENTED, 600, quote_index=_index("he"))

        assert ("9,000" in compact.text, compact.quotes.get("K1")) == (True, _INVENTED_QUOTE)

    async def test_should_fail_a_takeaway_whose_number_only_a_never_said_quote_holds(
        self, fake_llm: AsyncMock
    ) -> None:
        takeaways = [{"text": "The team shipped 9,000 apps by hand.", "ref": "K1"}]
        answer = _reply(keyTakeaways=[*takeaways, *_ANSWER["keyTakeaways"][1:]])
        fake_llm.replies.extend([answer, answer])  # type: ignore[attr-defined]
        request = _request(extraction=_INVENTED, quote_index=_index())

        assert await run_hero_rewrite(object(), request) is None  # type: ignore[arg-type]


# ─── D3 / D5: the prompt ───


class TestPrompt:
    async def test_should_take_both_line_examples_from_outside_the_sample(
        self, fake_llm: AsyncMock
    ) -> None:
        await run_hero_rewrite(object(), _request())  # type: ignore[arg-type]

        job = _job(fake_llm)
        assert ("Engineering leads" in job, "installs an OS" in job) == (False, False)

    async def test_should_ask_the_brief_for_a_shown_moment_never_for_its_delivery(
        self, fake_llm: AsyncMock
    ) -> None:
        await run_hero_rewrite(object(), _request())  # type: ignore[arg-type]

        job = _job(fake_llm)
        assert ("as it is delivered" in job, "Never a claim about delivery" in job) == (False, True)
