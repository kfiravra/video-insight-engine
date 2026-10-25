"""Phase-1 review fixes, lane C (assembly) — `gates/review-p1-findings.md`.

Moments keep the text a label was cut from and a borrowed sibling is the tab's
source (R4); an interview question's title is a sentence, never "Dr.", and rides
whole only when the title cuts it, and a Q&A list is no quote list (R7); every
key idea sits in a section and none carries an emoji (R2); the numbers strip is
the model's figures only and restored Moments are deduped (R13); a row with
evidence is no tag and the fallback takeaways are a checklist with no blank row
(R12, R11); a playbook that forbids the filmstrip gets none and folded rows never
cut the host's own items (R11); an unanchored claim shows no status (R9); a
scenario question keeps its timestamp (R10).
"""

from __future__ import annotations

from src.services.pipeline.assembly import assemble_response
from src.services.pipeline.assembly.assemblers_learning import key_idea_props
from src.services.pipeline.assembly.assemblers_overhaul import assemble_quiz_arena
from src.services.pipeline.assembly.assemblers_secondary import _normalize_claim
from src.services.pipeline.assembly.core import build_fallback_candidates
from src.services.pipeline.assembly.dedupe import _dedupe_tabs
from src.services.pipeline.assembly.info_grid_items import require_grid_values
from src.services.pipeline.assembly.ingredients import ingredient_row
from src.services.pipeline.assembly.moment_normalizers import normalize_moment_items
from src.services.pipeline.assembly.quote_spots import is_quote_list
from src.services.pipeline.assembly.stat_banner import _key_idea_tile, _stat_banner_attachment
from src.services.pipeline.assembly.steps_timeline import dedupe_under_steps_timeline
from src.shared_config.assembly_policy import injected_tab

_LABELS = [{"time": f"{m}:00", "seconds": 60 * m, "label": f"Chapter {m}"} for m in range(1, 41)]
_DESCRIBED = [
    {"timestamp": 1262, "description": "37signals goes pencils down on hand-written code; "
     "writing by hand is now an exceptional state."},
    {"timestamp": 2440, "description": "He announces he has retired from being a professional "
     "programmer and calls hand-chiseling code a hobby."},
    {"timestamp": 2715, "description": "Bring your own butler: every app must ship a CLI by "
     "next Friday so agents can drive it."},
]  # fmt: skip
_DESCRIBED_SECONDS = [1262, 2440, 2715]


def _tab(tab_id: str, component: str, source: str) -> dict:
    return {"id": tab_id, "label": tab_id.title(), "emoji": "", "component": component,
            "dataSource": source, "goal": "After this you know what each beat set."}  # fmt: skip


def _assemble(primary: str, tabs: list[dict], extraction: dict, **kwargs: object) -> dict:
    triage = {"primaryTag": primary, "contentTags": [primary], "tabs": tabs, **kwargs}
    meta = {"duration": 3780, "outline": [], "chapters": []}
    return assemble_response(triage, extraction, None, None, video_meta=meta)


def _ids(result: dict) -> list[str]:
    return [t["id"] for t in result["tabs"]]


# ─── R4: moments ───


class TestMomentDescriptions:
    def test_should_keep_a_short_description_when_the_label_was_cut_from_it(self) -> None:
        items = normalize_moment_items([{"timestamp": 30, "description": "He retires from code."}])

        assert items[0].get("description") == "He retires from code."

    def test_should_still_drop_a_description_that_repeats_the_sources_own_label(self) -> None:
        items = normalize_moment_items([{"seconds": 30, "label": "Intro", "description": "Intro"}])

        assert "description" not in items[0]


class TestBorrowedSibling:
    def test_should_record_the_borrowed_sibling_as_the_tabs_source_when_d6_drops_it(
        self,
    ) -> None:
        steps = [{"number": i, "instruction": f"Step {i} in full.", "timestamp": 60 * i}
                 for i in range(1, 6)]  # fmt: skip
        extraction = {
            "food": {"steps": steps},
            "learning": {"timestamps": _LABELS},
            "narrative": {"keyMoments": _DESCRIBED},
        }
        tabs = [
            _tab("steps", "step_player", "food.steps"),
            _tab("moments", "moment_track", "learning.timestamps"),
        ]

        result = _assemble("food", tabs, extraction)
        record = next(d for d in result["dropped"] if d["id"] == "moments")

        assert record["dataSource"] == "narrative.keyMoments"

    def test_should_let_no_second_tab_borrow_a_claimed_sibling(self) -> None:
        extraction = {"learning": {"timestamps": _LABELS}, "narrative": {"keyMoments": _DESCRIBED}}
        tabs = [
            _tab("timeline", "moment_track", "learning.timestamps"),
            _tab("beats", "moment_track", "learning.timestamps"),
        ]

        result = _assemble("learning", tabs, extraction)
        shipped = [
            t["id"]
            for t in result["tabs"]
            if [i["seconds"] for i in t["props"].get("items", [])] == _DESCRIBED_SECONDS
        ]

        assert (shipped, "beats" in _ids(result)) == (["timeline"], True)


# ─── R7: interview questions ───

_LONG_QUESTION = (
    "Dr. Cho, with everything you have seen across twenty years of clinical work and the "
    "trials you ran on the new compounds, what is the one thing you want parents to know? "
    "And how should they weigh it?"
)


class TestQuestionMoments:
    def test_should_title_a_long_question_by_a_sentence_not_an_abbreviation(self) -> None:
        items = normalize_moment_items(
            [{"timestamp": 60, "question": _LONG_QUESTION, "answer": "A."}]
        )

        assert items[0]["label"].startswith("Dr. Cho, with everything")

    def test_should_keep_the_question_when_it_is_the_whole_title(self) -> None:
        # Every exchange row carries its question (4.1): the web tells it from a moment.
        items = normalize_moment_items(
            [{"timestamp": 60, "question": "Why Rails?", "answer": "A."}]
        )

        assert items[0]["question"] == "Why Rails?"

    def test_should_keep_the_whole_question_when_the_title_cuts_it(self) -> None:
        items = normalize_moment_items(
            [{"timestamp": 60, "question": _LONG_QUESTION, "answer": "A."}]
        )

        assert items[0]["question"] == _LONG_QUESTION

    def test_should_not_read_an_interview_exchange_as_a_quote_list(self) -> None:
        qa = [{"question": "Why?", "answer": "Because.", "speaker": "Guest", "quote": None,
               "timestamp": 60}]  # fmt: skip

        assert is_quote_list(qa) is False


# ─── R2: key ideas ───


def _idea(claim: str, section: str | None = None, **extra: object) -> dict:
    return {"claim": claim, "why": f"Why {claim}", "timestamp": 60, "section": section, **extra}


class TestKeyIdeaSections:
    def test_should_put_the_unsectioned_ideas_in_a_trailing_other_section(self) -> None:
        props = key_idea_props([_idea("a", "One"), _idea("b"), _idea("c", "Two")])

        assert props["sections"][-1] == {"label": "Other", "spotIndices": [1]}

    def test_should_pass_no_emoji_onto_a_key_idea_card(self) -> None:
        spot = key_idea_props([_idea("a", emoji="🔥")])["spots"][0]

        assert "emoji" not in spot

    def test_should_pass_no_emoji_onto_a_figure_tile(self) -> None:
        idea = _idea("a", kind="number", figure={"value": "99%", "label": "less CPU"}, emoji="🔥")

        tile = _key_idea_tile(idea)

        assert tile is not None and "emoji" not in tile


# ─── R13: the numbers strip, restored Moments ───


class TestNumbersStrip:
    def test_should_mine_no_strip_from_key_point_titles(self) -> None:
        points = [{"title": f"{n},000 users in a month", "detail": "d", "timestamp": 60 * n}
                  for n in range(2, 10)]  # fmt: skip

        assert _stat_banner_attachment({"learning": {"keyPoints": points}}, "learning") is None


def _list_tab(tab_id: str, component: str, key: str, items: list[dict]) -> dict:
    return {"id": tab_id, "component": component, "props": {key: items}}


class TestRestoredMoments:
    def test_should_dedupe_the_moments_it_restores(self) -> None:
        texts = ["Boil the water", "Salt it", "Cook the pasta"]
        moments = [{"seconds": 60 * i, "label": f"Beat {i}", "description": "d"} for i in (1, 2, 3)]
        tabs = [
            _list_tab("list", "checklist", "items", [{"label": t} for t in texts]),
            _list_tab("steps", "step_player", "steps", [{"instruction": t} for t in texts]),
            _list_tab("moments", "moment_track", "items", moments),
            _list_tab("moments_2", "moment_track", "items", [dict(m) for m in moments]),
        ]

        dedupe_under_steps_timeline(tabs, [], "food", [], {}, _dedupe_tabs)

        assert [t["id"] for t in tabs] == ["list", "moments"]


# ─── R12 / R11: grids and the fallback takeaways ───


class TestGridRows:
    def test_should_ship_a_short_key_with_evidence_as_a_valued_row_not_a_tag(self) -> None:
        rows = [{"key": "Rust", "evidence": "99% less CPU"}, {"key": "Go", "evidence": "fast"}]
        tab = _list_tab("stack", "info_grid", "items", rows)

        require_grid_values([tab], [])

        assert ("mode" not in tab["props"], tab["props"]["items"][0]["value"]) == (
            True,
            "99% less CPU",
        )


class TestFallbackTakeaways:
    def test_should_offer_the_takeaways_as_a_checklist_with_the_registry_goal(self) -> None:
        key_info = build_fallback_candidates({"keyTakeaways": ["a", "b"]}, {"overview"}, set())[0]

        assert (key_info["component"], key_info["goal"]) == (
            "checklist",
            injected_tab("keyInfo")["goal"],
        )

    def test_should_never_ship_a_blank_takeaway_row(self) -> None:
        synthesis = {"keyTakeaways": ["a", "  ", "b"]}

        key_info = build_fallback_candidates(synthesis, {"overview"}, set())[0]

        assert [row["label"] for row in key_info["props"]["items"]] == ["a", "b"]

    def test_should_offer_no_takeaways_tab_with_one_stated_takeaway(self) -> None:
        assert build_fallback_candidates({"keyTakeaways": ["a", ""]}, {"overview"}, set()) == []


class TestIngredientAmount:
    def test_should_treat_an_unstated_amount_as_absent(self) -> None:
        row = ingredient_row({"name": "salt", "amount": None, "unit": "", "displayAmount": ""})

        assert row["label"] == "salt"


# ─── R11: the filmstrip a playbook forbids, folds under a cap ───

_FRAMES = [{"timestamp": float(i * 30), "s3_url": f"https://f/{i}.jpg", "index": i,
            "ocr_text": f"Slide {i}"} for i in range(12)]  # fmt: skip


class TestForbiddenFilmstrip:
    def test_should_append_no_filmstrip_when_the_playbook_forbids_it(self) -> None:
        extraction = {"learning": {"keyIdeas": [_idea(f"Idea {i}") for i in range(4)]}}
        triage = {"primaryTag": "learning", "contentTags": ["learning"], "contentFormat": "talk",
                  "tabs": [_tab("key_ideas", "spot_explorer", "learning.keyIdeas")]}  # fmt: skip

        result = assemble_response(
            triage, extraction, None, None, video_meta={"duration": 600},
            frames=_FRAMES, gallery_frames=_FRAMES,
        )  # fmt: skip

        assert "frames-gallery" not in _ids(result)


class TestFoldUnderTheCap:
    def test_should_keep_every_capped_key_idea_when_a_grid_folds_into_the_tab(self) -> None:
        ideas = [{**_idea(f"Idea {i}"), "timestamp": 60 * i} for i in range(24)]
        points = [{"title": f"Number {i}", "detail": f"{i}0 percent"} for i in range(5)]
        host = {**_tab("key_ideas", "spot_explorer", "learning.keyIdeas"),
                "folds": [{"id": "numbers", "label": "Numbers", "dataSource": "learning.keyPoints",
                           "component": "info_grid"}]}  # fmt: skip

        result = _assemble(
            "learning", [host], {"learning": {"keyIdeas": ideas, "keyPoints": points}}
        )
        spots = next(t for t in result["tabs"] if t["id"] == "key_ideas")["props"]["spots"]

        assert sum(1 for s in spots if s["name"].startswith("Idea")) == 24


# ─── R9: claims · R10: scenario quiz ───


class TestUnanchoredClaim:
    def test_should_ship_an_unanchored_claim_without_status_or_citation(self) -> None:
        claim = {"claim": "X works", "status": "contested", "sourceCitation": "A study"}

        assert _normalize_claim(claim) == {"claim": "X works"}

    def test_should_keep_an_anchored_claims_status_and_citation(self) -> None:
        # Anchored and quoted (2.0 c): the status and its citation ship — and the
        # quote itself (4.2).
        claim = {
            "claim": "X",
            "status": "contested",
            "sourceCitation": "A study",
            "quote": "X, they say.",
            "timestamp": 90,
        }

        assert _normalize_claim(claim) == {
            "claim": "X",
            "quote": "X, they say.",
            "status": "contested",
            "sourceCitation": "A study",
            "timestamp": 90,
        }


class TestScenarioQuiz:
    def test_should_keep_a_scenario_questions_timestamp(self) -> None:
        options = [{"text": "Read the log", "correct": True}, {"text": "Retry"}]
        scenario = {
            "situation": "The build fails. What first?",
            "timestamp": 95,
            "options": options,
        }

        props = assemble_quiz_arena({}, [scenario], {}, None)

        assert props is not None and props["questions"][0]["timestamp"] == 95
