"""Phase-C review, the prompts lane's finding 3 (code half), lane FB: a talk's asks
checklist waits for the key ideas, never for takeaways.

On a talk (the probe's format) the "What they asked" checklist on ``learning.takeaways``
is a view of the key ideas of kind ask (``talk_asks.asks_data``), so the split extraction
requests ``learning.keyIdeas`` for it: its brief rides on the key-ideas group's tab lines
and no takeaways are paid for (S01 gC wrote 9, all discarded). A lecture's or an
explainer's Takeaways checklist still requests takeaways. The split run and the
failed-group relabel both pass the probe's format, and the asks tab's line in the key-ideas
group's job names ``learning.keyIdeas (kind ask)``, never takeaways.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

from src.services.pipeline.extraction_group_prompt import tabs_to_serve
from src.services.pipeline.extraction_groups import GroupCall, GroupOutcome, SplitRun
from src.services.pipeline.extraction_prompt import render_tabs_to_serve
from src.services.pipeline.field_requests import FieldRequest, requested_fields
from src.services.pipeline.group_failures import group_drops
from src.services.pipeline.schema_fields import scalar_fields
from tests.test_split_extraction import _ctx, _done, _drain, _Groups, _plan

_KEY_IDEAS = "learning.keyIdeas"
_TAKEAWAYS = "learning.takeaways"
_KEY_IDEAS_TAB = {
    "id": "key_ideas",
    "label": "Key ideas",
    "component": "spot_explorer",
    "dataSource": _KEY_IDEAS,
    "brief": {"what": "each claim", "expect": 12},
}
_ASKS_TAB = {
    "id": "asks",
    "label": "What they asked",
    "component": "checklist",
    "dataSource": _TAKEAWAYS,
    "brief": {"what": "only the explicit asks", "expect": 4},
}
_TALK_TABS = [_KEY_IDEAS_TAB, _ASKS_TAB]


def _requests(tabs: list[dict[str, Any]], content_format: str | None) -> dict[str, FieldRequest]:
    fields = requested_fields(
        tabs, ["learning"], [], scalar_fields, primary_tag="learning", content_format=content_format
    )
    return {request.path: request for request in fields}


class TestTalkAsksRequests:
    def test_should_request_no_takeaways_when_a_talk_plans_the_asks_checklist(self) -> None:
        assert _TAKEAWAYS not in _requests(_TALK_TABS, "talk")

    def test_should_list_the_asks_tab_under_key_ideas_when_the_run_is_a_talk(self) -> None:
        assert _requests(_TALK_TABS, "talk")[_KEY_IDEAS].tab_ids == ("key_ideas", "asks")

    def test_should_plan_the_key_ideas_for_the_asks_tab_when_a_talk_plans_no_key_ideas_tab(
        self,
    ) -> None:
        request = _requests([_ASKS_TAB], "talk")[_KEY_IDEAS]

        assert (request.reason, request.tab_ids, request.expect) == ("planned", ("asks",), 4)

    def test_should_still_request_takeaways_when_a_lecture_plans_a_takeaways_checklist(
        self,
    ) -> None:
        assert _requests(_TALK_TABS, "lecture")[_TAKEAWAYS].tab_ids == ("asks",)

    def test_should_still_request_takeaways_when_the_runs_format_is_unknown(self) -> None:
        assert _TAKEAWAYS in _requests(_TALK_TABS, None)


_ASKS_ON_KEY_IDEAS = "- What they asked — checklist ← learning.keyIdeas (kind ask) — "
_ASKS_ON_TAKEAWAYS = "- What they asked — checklist ← learning.takeaways — "


class TestTalkAsksLine:
    def test_should_point_the_asks_line_at_the_key_ideas_when_the_run_is_a_talk(self) -> None:
        lines = render_tabs_to_serve([_ASKS_TAB], ["learning"], content_format="talk")

        assert lines.startswith(_ASKS_ON_KEY_IDEAS)

    def test_should_keep_the_takeaways_line_when_a_lecture_serves_a_takeaways_checklist(
        self,
    ) -> None:
        lines = render_tabs_to_serve([_ASKS_TAB], ["learning"], content_format="lecture")

        assert lines.startswith(_ASKS_ON_TAKEAWAYS)


class _Asked(_Groups):
    """The split run's group calls, recording every field path a group asked for and
    every group job's tab lines."""

    def __init__(self) -> None:
        super().__init__()
        self.paths: list[str] = []
        self.lines: list[str] = []

    async def __call__(self, call: GroupCall, run: SplitRun, **kwargs: Any) -> GroupOutcome:
        self.paths.extend(field.path for field in call.group.fields)
        self.lines.extend(tabs_to_serve(call.group, call.job).splitlines())
        return await super().__call__(call, run, **kwargs)


class TestCallersPassTheFormat:
    async def test_should_ask_no_group_for_takeaways_when_a_talks_split_run_has_an_asks_tab(
        self,
    ) -> None:
        ctx, asked = _done(_ctx(["learning"], _TALK_TABS)), _Asked()
        ctx.content_format = "talk"

        await _drain(ctx, asked)

        assert (_KEY_IDEAS in asked.paths, _TAKEAWAYS in asked.paths) == (True, False)

    async def test_should_name_the_key_ideas_on_the_asks_line_when_a_talks_split_run_serves_it(
        self,
    ) -> None:
        ctx, asked = _done(_ctx(["learning"], _TALK_TABS)), _Asked()
        ctx.content_format = "talk"

        await _drain(ctx, asked)

        assert any(line.startswith(_ASKS_ON_KEY_IDEAS) for line in asked.lines)

    def test_should_relabel_the_asks_tabs_drop_when_a_talks_key_ideas_group_failed(self) -> None:
        rows = [{"group": "text1", "chunk": 0, "fields": [_KEY_IDEAS], "status": "failed"}]
        ctx = SimpleNamespace(
            plan_result=_plan(["learning"], _TALK_TABS),
            extraction_groups=rows,
            content_format="talk",
        )
        drop = {"id": "asks", "component": "checklist", "reason": "assembler_returned_none"}

        [relabeled] = group_drops(ctx, [drop])  # type: ignore[arg-type]

        assert relabeled["reason"] == "extraction_group_failed"
