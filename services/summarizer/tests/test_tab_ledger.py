"""The tab_ready emission contract (pipeline-1min 2.1) — ``TabLedger`` rules.

final = the tab's last emission this run; a tab a late phase may still change
goes out non-final; a provisional tab is never final; the closing re-emit sends
every tab not yet received final, once, at its final position. The tab that
takes the provisional's place under another id names it in ``replaces``.
"""

from __future__ import annotations

import json

from src.services.pipeline.tab_ledger import TabLedger


def _tab(tab_id: str, component: str = "info_grid", **extra: object) -> dict:
    return {"id": tab_id, "component": component, "props": {"items": []}, **extra}


def _payload(chunk: str) -> dict:
    return json.loads(chunk.removeprefix("data: "))


def _ledger(*, quiz: bool = False, synthesis: bool = False) -> TabLedger:
    ledger = TabLedger()
    ledger.expect_late_changes(quiz=quiz, synthesis=synthesis)
    return ledger


class TestEvent:
    def test_should_mark_a_tab_final_when_no_late_phase_can_change_it(self) -> None:
        assert _payload(_ledger().event(_tab("facts"), 2))["final"] is True

    def test_should_mark_every_tab_non_final_while_the_quiz_is_pending(self) -> None:
        ledger = _ledger(quiz=True)

        finals = [_payload(ledger.event(_tab(i), n))["final"] for n, i in enumerate("abc")]

        assert finals == [False, False, False]

    def test_should_mark_the_overview_non_final_while_a_late_synthesis_is_pending(self) -> None:
        ledger = _ledger(synthesis=True)

        overview = _payload(ledger.event(_tab("overview", "overview"), 0))

        assert overview["final"] is False

    def test_should_mark_other_tabs_final_while_only_synthesis_is_pending(self) -> None:
        assert _payload(_ledger(synthesis=True).event(_tab("facts"), 1))["final"] is True

    def test_should_never_mark_a_provisional_tab_final(self) -> None:
        payload = _payload(_ledger().event(_tab("moments", "moment_track"), 3, provisional=True))

        assert (payload["provisional"], payload["final"]) == (True, False)

    def test_should_carry_the_position_on_the_wire(self) -> None:
        assert _payload(_ledger().event(_tab("facts"), 4))["position"] == 4

    def test_should_leave_the_tab_dict_untouched(self) -> None:
        tab = _tab("facts")

        _ledger().event(tab, 1)

        assert set(tab) == {"id", "component", "props"}

    def test_should_omit_provisional_on_a_real_tab(self) -> None:
        assert "provisional" not in _payload(_ledger().event(_tab("facts"), 1))


class TestClosingEvents:
    def test_should_send_every_non_final_tab_final_in_order(self) -> None:
        ledger = _ledger(quiz=True)
        tabs = [_tab("overview", "overview"), _tab("code"), _tab("quiz", "quiz_arena")]
        for position, tab in enumerate(tabs[:2]):
            ledger.event(tab, position)

        closing = [_payload(c) for c in ledger.closing_events(tabs)]

        assert [(p["id"], p["position"], p["final"]) for p in closing] == [
            ("overview", 0, True),
            ("code", 1, True),
            ("quiz", 2, True),
        ]

    def test_should_skip_a_tab_already_received_final(self) -> None:
        ledger = _ledger()
        tabs = [_tab("facts"), _tab("tips")]
        ledger.event(tabs[0], 0)

        closing = [_payload(c)["id"] for c in ledger.closing_events(tabs)]

        assert closing == ["tips"]

    def test_should_replace_a_provisional_tab_by_id_with_a_final_one(self) -> None:
        ledger = _ledger()
        ledger.event(_tab("moments", "moment_track", label="outline"), 2, provisional=True)
        real = _tab("moments", "moment_track", label="extracted")

        closing = [_payload(c) for c in ledger.closing_events([_tab("a"), _tab("b"), real])]

        moments = next(p for p in closing if p["id"] == "moments")
        assert (moments["label"], moments["final"], "provisional" in moments) == (
            "extracted",
            True,
            False,
        )

    def test_should_send_the_patched_overview_once_synthesis_landed(self) -> None:
        ledger = _ledger(synthesis=True)
        overview = _tab("overview", "overview", props={"data": {"masterSummary": ""}})
        ledger.event(overview, 0)
        overview["props"]["data"]["masterSummary"] = "patched"

        closing = [_payload(c) for c in ledger.closing_events([overview])]

        assert closing[0]["props"]["data"]["masterSummary"] == "patched"

    def test_should_re_send_a_final_tab_that_changed_after_its_final_emission(self) -> None:
        ledger = _ledger()
        tab = _tab("facts")
        ledger.event(tab, 0)
        tab["props"]["items"] = ["changed"]

        closing = [_payload(c)["id"] for c in ledger.closing_events([tab])]

        assert closing == ["facts"]

    def test_should_send_each_tab_final_exactly_once_over_the_run(self) -> None:
        ledger = _ledger(quiz=True)
        tabs = [_tab("overview", "overview"), _tab("code")]
        sent = [ledger.event(tab, i) for i, tab in enumerate(tabs)]
        sent += ledger.closing_events(tabs)
        sent += ledger.closing_events(tabs)

        finals = [_payload(c)["id"] for c in sent if _payload(c)["final"]]

        assert finals == ["overview", "code"]


def _with_provisional(provisional_id: str = "key_moments") -> TabLedger:
    ledger = _ledger()
    ledger.event(_tab(provisional_id, "moment_track"), 2, provisional=True)
    return ledger


class TestReplaces:
    def test_should_name_the_provisional_on_the_tab_taking_its_place_under_another_id(
        self,
    ) -> None:
        ledger = _with_provisional()
        response = [_tab("overview", "overview"), _tab("segments", "moment_track")]
        ledger.track_response(response)

        payload = _payload(ledger.event(response[1], 1))

        assert payload["replaces"] == "key_moments"

    def test_should_name_it_on_the_first_emission_only(self) -> None:
        ledger = _with_provisional()
        segments = _tab("segments", "moment_track")
        ledger.track_response([segments])
        ledger.event(segments, 0)

        later = _payload(ledger.event(segments, 0))

        assert "replaces" not in later

    def test_should_name_nothing_when_a_tab_keeps_the_provisional_id(self) -> None:
        ledger = _with_provisional("moments")
        response = [_tab("moments", "moment_track"), _tab("segments", "moment_track")]
        ledger.track_response(response)

        sent = [_payload(ledger.event(tab, i)) for i, tab in enumerate(response)]

        assert [p.get("replaces") for p in sent] == [None, None]

    def test_should_name_nothing_on_a_tab_of_another_component(self) -> None:
        ledger = _with_provisional()
        facts = _tab("facts")
        ledger.track_response([facts])

        assert "replaces" not in _payload(ledger.event(facts, 0))

    def test_should_name_nothing_without_a_provisional_tab(self) -> None:
        ledger = _ledger()
        segments = _tab("segments", "moment_track")
        ledger.track_response([segments])

        assert "replaces" not in _payload(ledger.event(segments, 0))

    def test_should_name_it_in_the_final_re_emit_when_the_tab_first_comes_there(self) -> None:
        ledger = _with_provisional()

        closing = [_payload(c) for c in ledger.closing_events([_tab("segments", "moment_track")])]

        assert (closing[0]["id"], closing[0]["replaces"]) == ("segments", "key_moments")


class TestWithdrawn:
    def test_should_name_streamed_tabs_the_final_response_no_longer_holds(self) -> None:
        ledger = _ledger(quiz=True)
        ledger.event(_tab("key_moments", "moment_track"), 3, provisional=True)
        ledger.event(_tab("key_info"), 2)

        assert ledger.withdrawn([_tab("overview", "overview"), _tab("key_info")]) == ["key_moments"]

    def test_should_withdraw_nothing_when_every_streamed_tab_survived(self) -> None:
        ledger = _ledger()
        ledger.event(_tab("facts"), 0)

        assert ledger.withdrawn([_tab("facts")]) == []
