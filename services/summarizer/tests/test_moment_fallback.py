"""moment_track is never dropped for lack of data: memory outline → chapters → description timestamps."""

from __future__ import annotations

from src.services.pipeline.assembly import assemble_response
from src.services.pipeline.assembly.moment_fallback import fallback_moments

_OUTLINE = [
    {"start": 0, "end": 300, "title": "The stack"},
    {"start": 300, "end": 900, "title": "Agent workflow"},
]
_CHAPTERS = [
    {"start_time": 0, "end_time": 450, "title": "Intro"},
    {"start_time": 450, "end_time": 900, "title": "Demo"},
]
_DESCRIPTION = {
    "timestamps": [
        {"time": "5:00", "seconds": 300, "label": "Second part"},
        {"time": "0:00", "seconds": 0, "label": "Start"},
    ]
}


def _labels(moments: list[dict] | None) -> list[str]:
    return [m["label"] for m in moments or []]


class TestFallbackOrder:
    def test_should_prefer_the_memory_outline_over_chapters(self) -> None:
        meta = {"duration": 900, "outline": _OUTLINE, "chapters": _CHAPTERS}

        assert _labels(fallback_moments(meta, _DESCRIPTION)) == ["The stack", "Agent workflow"]

    def test_should_use_chapters_when_there_is_no_outline(self) -> None:
        meta = {"duration": 900, "outline": [], "chapters": _CHAPTERS}

        assert _labels(fallback_moments(meta, _DESCRIPTION)) == ["Intro", "Demo"]

    def test_should_use_description_timestamps_last_in_time_order(self) -> None:
        meta = {"duration": 900, "outline": [], "chapters": []}

        assert _labels(fallback_moments(meta, _DESCRIPTION)) == ["Start", "Second part"]

    def test_should_skip_entries_past_the_end_of_the_video(self) -> None:
        meta = {"duration": 200, "outline": _OUTLINE}

        assert _labels(fallback_moments(meta, None)) == ["The stack"]

    def test_should_return_none_when_no_source_has_entries(self) -> None:
        assert fallback_moments({"duration": 900}, None) is None


def _triage(data_source: str = "learning.timestamps", tags: list[str] | None = None) -> dict:
    return {
        "contentTags": tags or ["tech"],
        "primaryTag": (tags or ["tech"])[0],
        "tabs": [
            {
                "id": "moments",
                "label": "Key Moments",
                "emoji": "🎬",
                "component": "moment_track",
                "dataSource": data_source,
            }
        ],
    }


def _moment_tab(result: dict) -> dict | None:
    return next((t for t in result["tabs"] if t["component"] == "moment_track"), None)


class TestAssemblyNeverDropsMomentTrack:
    def test_should_fill_moments_from_the_outline_for_a_tech_only_video(self) -> None:
        """T1dQhQAm8Tc (prod 2026-10-09): moments planned on learning.timestamps, extraction is tech."""
        extraction = {"tech": {"topics": ["agents"]}}
        meta = {"duration": 900, "outline": _OUTLINE, "chapters": []}

        result = assemble_response(_triage(), extraction, None, None, video_meta=meta)

        assert _labels((_moment_tab(result) or {}).get("props", {}).get("items")) == [
            "The stack",
            "Agent workflow",
        ]

    def test_should_fill_moments_from_description_timestamps_when_nothing_else_exists(self) -> None:
        meta = {"duration": 900, "outline": [], "chapters": []}

        result = assemble_response(
            _triage(), {"tech": {}}, None, None, video_meta=meta, description_analysis=_DESCRIPTION
        )

        assert _moment_tab(result) is not None

    def test_should_refill_a_tab_whose_moments_all_lie_past_the_end(self) -> None:
        extraction = {
            "learning": {"timestamps": [{"time": "90:00", "seconds": 5400, "label": "Bogus"}]}
        }
        meta = {"duration": 900, "outline": _OUTLINE}

        result = assemble_response(
            _triage(tags=["learning"]), extraction, None, None, video_meta=meta
        )

        assert _labels((_moment_tab(result) or {}).get("props", {}).get("items")) == [
            "The stack",
            "Agent workflow",
        ]

    def test_should_keep_extracted_moments_when_they_exist(self) -> None:
        extraction = {
            "learning": {"timestamps": [{"time": "1:00", "seconds": 60, "label": "Real"}]}
        }
        meta = {"duration": 900, "outline": _OUTLINE}

        result = assemble_response(
            _triage(tags=["learning"]), extraction, None, None, video_meta=meta
        )

        assert _labels((_moment_tab(result) or {}).get("props", {}).get("items")) == ["Real"]

    def test_should_drop_the_tab_only_when_no_source_has_any_moment(self) -> None:
        result = assemble_response(
            _triage(), {"tech": {}}, None, None, video_meta={"duration": 900}
        )

        assert _moment_tab(result) is None
