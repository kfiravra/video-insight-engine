"""Quiz v2's key ideas per domain (output-quality p2.1b; split out of
``test_p2_1b_quiz_v2.py`` at the p3 review, A8 — that module stays under 500 lines).

Which registered dataSource a domain's questions test, how its items read in the
prompt, and which transcript lines go when the transcript is over budget. No LLM.
"""

from __future__ import annotations

import pytest

from src.services.pipeline import quiz_v2_prompt
from src.services.pipeline.quiz_v2_ideas import KEY_IDEA_SOURCES, NO_KEY_IDEAS, key_idea_items
from src.shared_config.domain_config import data_source, quiz_enrichment
from tests.test_p2_1b_quiz_v2 import _EXTRACTION, _TRANSCRIPT, _prompt, _prompts


class TestKeyIdeaSources:
    def test_should_name_only_registered_data_sources(self) -> None:
        sources = {source for chain in KEY_IDEA_SOURCES.values() for source in chain}

        assert [source for source in sorted(sources) if data_source(source) is None] == []

    def test_should_cover_every_quiz_domain(self) -> None:
        assert set(quiz_enrichment()["quizDomains"]) <= set(KEY_IDEA_SOURCES)

    def test_should_read_learning_key_ideas_first_when_a_podcast_carries_them(self) -> None:
        extraction = {**_EXTRACTION, "podcast": {"claims": [{"claim": "Other"}]}}

        assert key_idea_items("podcast", extraction) == _EXTRACTION["learning"]["keyIdeas"]

    def test_should_fall_back_to_the_domains_claims_when_key_ideas_are_empty(self) -> None:
        extraction = {"learning": {"keyIdeas": []}, "podcast": {"claims": [{"claim": "C"}]}}

        assert key_idea_items("podcast", extraction) == [{"claim": "C"}]

    def test_should_name_the_speaker_of_a_key_idea_when_one_is_given(self) -> None:
        idea = {"claim": "We are alone", "why": "Fermi", "timestamp": 181, "speaker": "Max"}
        extraction = {"learning": {"keyIdeas": [idea]}}

        assert "- K1 [3:01] (Max) We are alone — Fermi" in _prompt(extraction=extraction)

    def test_should_say_so_when_the_run_has_no_key_ideas(self) -> None:
        assert NO_KEY_IDEAS in _prompt(extraction={"learning": {"keyIdeas": []}})

    def test_should_send_only_the_lines_near_key_ideas_when_the_transcript_is_over_budget(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(quiz_v2_prompt, "TRANSCRIPT_MAX_CHARS", 200)
        monkeypatch.setattr(quiz_v2_prompt, "KEY_IDEA_WINDOW_S", 20)

        sent = [
            line
            for prompt in _prompts()
            for line in prompt.split("<transcript>\n", 1)[1]
            .split("\n</transcript>", 1)[0]
            .splitlines()
        ]

        near = [line for line in _TRANSCRIPT.splitlines() if line.startswith(("[0:30]", "[4:00]"))]
        assert sent == near
