"""Integration tests: frame intelligence pipeline chain.

Verifies the full data flow:
  vision descriptions + OCR → <visual_annotations> block → assembly

Uses realistic data fixtures to simulate the pipeline without real LLM/network calls.
"""

import json

from src.services.pipeline.visual_annotations import (
    annotation_entries,
    render_visual_annotations,
)
from src.services.pipeline.assembly import (
    assemble_response,
    find_description_for_frame,
)
from src.services.media.frame_analyzer import parse_vision_response


# ─────────────────────────────────────────────────────────────────────────────
# Realistic Fixtures
# ─────────────────────────────────────────────────────────────────────────────

_FRAME_DESCRIPTIONS = [
    {
        "frame_index": 0,
        "scene_type": "code",
        "content": "Python binary search function with low/high pointer variables",
        "text_visible": "def binary_search(arr, target):\n    low, high = 0, len(arr) - 1",
        "educational_value": "Shows the exact implementation being discussed",
        "timestamp_sec": 12.0,
        "s3_url": "https://s3.example.com/frame_012.jpg",
        "original_index": 12,
    },
    {
        "frame_index": 1,
        "scene_type": "talking_head",
        "content": "Presenter speaking to camera",
        "text_visible": "",
        "educational_value": None,
        "timestamp_sec": 25.0,
        "s3_url": "https://s3.example.com/frame_025.jpg",
        "original_index": 25,
    },
    {
        "frame_index": 2,
        "scene_type": "diagram",
        "content": "Flowchart showing binary search decision tree with array halving",
        "text_visible": "O(log n)",
        "educational_value": "Visualizes how the search space shrinks at each step",
        "timestamp_sec": 48.0,
        "s3_url": "https://s3.example.com/frame_048.jpg",
        "original_index": 48,
    },
    {
        "frame_index": 3,
        "scene_type": "slide",
        "content": "Summary slide with key takeaways",
        "text_visible": "Key Takeaways:\n1. O(log n)\n2. Sorted array required",
        "educational_value": "Summarizes the main points of the tutorial",
        "timestamp_sec": 57.0,
        "s3_url": "https://s3.example.com/frame_057.jpg",
        "original_index": 57,
    },
]

_ALL_FRAMES = [
    {
        "index": 3,
        "timestamp": 3.0,
        "ocr_text": "Python Binary Search Tutorial",
        "s3_url": "https://s3.example.com/frame_003.jpg",
    },
    {
        "index": 5,
        "timestamp": 8.0,
        "ocr_text": "binary_search.py — VS Code",
        "s3_url": "https://s3.example.com/frame_005.jpg",
    },
    {
        "index": 10,
        "timestamp": 10.0,
        "ocr_text": "def binary_search(arr, target):",
        "s3_url": "https://s3.example.com/frame_010.jpg",
    },
    {
        "index": 12,
        "timestamp": 12.0,
        "ocr_text": "def binary_search(arr, target):",
        "s3_url": "https://s3.example.com/frame_012.jpg",
    },
    {
        "index": 15,
        "timestamp": 15.0,
        "ocr_text": "low = 0, high = len(arr) - 1",
        "s3_url": "https://s3.example.com/frame_015.jpg",
    },
    {
        "index": 20,
        "timestamp": 20.0,
        "ocr_text": "while low <= high:",
        "s3_url": "https://s3.example.com/frame_020.jpg",
    },
    {
        "index": 25,
        "timestamp": 25.0,
        "ocr_text": None,
        "s3_url": "https://s3.example.com/frame_025.jpg",
    },
    {
        "index": 30,
        "timestamp": 30.0,
        "ocr_text": "Time Complexity Analysis",
        "s3_url": "https://s3.example.com/frame_030.jpg",
    },
    {
        "index": 35,
        "timestamp": 35.0,
        "ocr_text": "Edge cases to consider:",
        "s3_url": "https://s3.example.com/frame_035.jpg",
    },
    {
        "index": 40,
        "timestamp": 40.0,
        "ocr_text": "Empty array → return -1",
        "s3_url": "https://s3.example.com/frame_040.jpg",
    },
    {
        "index": 48,
        "timestamp": 48.0,
        "ocr_text": "O(log n)",
        "s3_url": "https://s3.example.com/frame_048.jpg",
    },
    {
        "index": 53,
        "timestamp": 53.0,
        "ocr_text": "Search space halving diagram",
        "s3_url": "https://s3.example.com/frame_053.jpg",
    },
    {
        "index": 57,
        "timestamp": 57.0,
        "ocr_text": "Key Takeaways",
        "s3_url": "https://s3.example.com/frame_057.jpg",
    },
]

_GALLERY_FRAMES = [
    {
        "index": 3,
        "timestamp": 3.0,
        "s3_url": "https://s3.example.com/frame_003.jpg",
        "ocr_text": "Python Binary Search Tutorial",
    },
    {
        "index": 10,
        "timestamp": 10.0,
        "s3_url": "https://s3.example.com/frame_010.jpg",
        "ocr_text": "def binary_search(arr, target):",
    },
    {
        "index": 12,
        "timestamp": 12.0,
        "s3_url": "https://s3.example.com/frame_012.jpg",
        "ocr_text": "def binary_search(arr, target):",
    },
    {
        "index": 15,
        "timestamp": 15.0,
        "s3_url": "https://s3.example.com/frame_015.jpg",
        "ocr_text": "low = 0, high = len(arr) - 1",
    },
    {
        "index": 20,
        "timestamp": 20.0,
        "s3_url": "https://s3.example.com/frame_020.jpg",
        "ocr_text": "while low <= high:",
    },
    {
        "index": 30,
        "timestamp": 30.0,
        "s3_url": "https://s3.example.com/frame_030.jpg",
        "ocr_text": "Time Complexity Analysis",
    },
    {
        "index": 35,
        "timestamp": 35.0,
        "s3_url": "https://s3.example.com/frame_035.jpg",
        "ocr_text": "Edge cases to consider:",
    },
    {
        "index": 40,
        "timestamp": 40.0,
        "s3_url": "https://s3.example.com/frame_040.jpg",
        "ocr_text": "Empty array → return -1",
    },
    {
        "index": 48,
        "timestamp": 48.0,
        "s3_url": "https://s3.example.com/frame_048.jpg",
        "ocr_text": "O(log n)",
    },
    {
        "index": 53,
        "timestamp": 53.0,
        "s3_url": "https://s3.example.com/frame_053.jpg",
        "ocr_text": "Search space halving diagram",
    },
    {
        "index": 57,
        "timestamp": 57.0,
        "s3_url": "https://s3.example.com/frame_057.jpg",
        "ocr_text": "Key Takeaways",
    },
]


# ─────────────────────────────────────────────────────────────────────────────
# Integration: the <visual_annotations> block
# ─────────────────────────────────────────────────────────────────────────────


def _entry_lines(block: str) -> list[str]:
    """First line of every entry (continuation lines of multi-line text dropped)."""
    return [entry.text.split("\n")[0] for entry in annotation_entries(block)]


class TestVisualAnnotationsIntegration:
    """The annotations block rendered from realistic vision + OCR data."""

    def test_should_caption_every_described_frame_at_its_time(self):
        lines = _entry_lines(render_visual_annotations(_FRAME_DESCRIPTIONS, _ALL_FRAMES))

        assert {
            "[0:12] Python binary search function with low/high pointer variables"
            " | def binary_search(arr, target):",
            "[0:48] Flowchart showing binary search decision tree with array halving | O(log n)",
            "[0:57] Summary slide with key takeaways | Key Takeaways:",
        } <= set(lines)

    def test_should_filter_talking_heads_without_educational_value(self):
        block = render_visual_annotations(_FRAME_DESCRIPTIONS, _ALL_FRAMES)

        assert "Presenter speaking" not in block

    def test_should_give_frames_vision_skipped_an_ocr_only_entry(self):
        lines = _entry_lines(render_visual_annotations(_FRAME_DESCRIPTIONS, _ALL_FRAMES))

        assert {"[0:08] | binary_search.py — VS Code", "[0:35] | Edge cases to consider:"} <= set(
            lines
        )

    def test_should_not_repeat_ocr_for_frames_vision_described(self):
        entries = annotation_entries(render_visual_annotations(_FRAME_DESCRIPTIONS, _ALL_FRAMES))

        assert [entry.seconds for entry in entries].count(48) == 1

    def test_should_list_entries_chronologically(self):
        entries = annotation_entries(render_visual_annotations(_FRAME_DESCRIPTIONS, _ALL_FRAMES))
        seconds = [entry.seconds for entry in entries]

        assert seconds == sorted(seconds)

    def test_should_render_three_captioned_and_nine_ocr_only_entries(self):
        lines = _entry_lines(render_visual_annotations(_FRAME_DESCRIPTIONS, _ALL_FRAMES))
        ocr_only = [line for line in lines if line.split("] ", 1)[1].startswith("| ")]

        assert (len(lines) - len(ocr_only), len(ocr_only)) == (3, 9)

    def test_should_render_only_ocr_entries_without_vision(self):
        lines = _entry_lines(render_visual_annotations([], _ALL_FRAMES))

        assert all(line.split("] ", 1)[1].startswith("| ") for line in lines)


# ─────────────────────────────────────────────────────────────────────────────
# Integration: assembly with frame_descriptions
# ─────────────────────────────────────────────────────────────────────────────


class TestAssemblyWithFrameDescriptions:
    """Test that assembly uses frame_descriptions for gallery captions."""

    def _triage_with_overview(self):
        return {
            "contentTags": ["tech"],
            "primaryTag": "tech",
            "userGoal": "Learn binary search",
            "modifiers": [],
            "tabs": [
                {"id": "overview", "label": "Overview", "emoji": "📖", "dataSource": "tech"},
                {"id": "code", "label": "Code", "emoji": "💻", "dataSource": "tech.snippets"},
            ],
        }

    def _extraction(self):
        return {
            "tech": {
                "languages": ["Python"],
                "snippets": [
                    {
                        "language": "python",
                        "code": "def binary_search():",
                        "explanation": "Main function",
                    }
                ],
            },
        }

    def test_gallery_captions_from_vision_descriptions(self):
        """Gallery images should use vision content for captions when available."""
        result = assemble_response(
            triage=self._triage_with_overview(),
            extraction=self._extraction(),
            enrichment=None,
            synthesis={"tldr": "A binary search tutorial"},
            frames=_ALL_FRAMES,
            gallery_frames=_GALLERY_FRAMES,
            all_frames=_ALL_FRAMES,
            frame_descriptions=_FRAME_DESCRIPTIONS,
        )

        # Find the gallery tab
        gallery_tab = None
        for tab in result["tabs"]:
            if tab["id"] == "frames-gallery":
                gallery_tab = tab
                break

        assert gallery_tab is not None, "Gallery tab not found"
        images = gallery_tab["props"]["frames"]
        assert len(images) == len(_GALLERY_FRAMES)

        # Frame at 12s should have vision description as caption
        frame_12 = next(img for img in images if img["timestamp"] == 12.0)
        assert "binary search function" in frame_12["caption"].lower()

        # Frame at 48s should have vision description as caption
        frame_48 = next(img for img in images if img["timestamp"] == 48.0)
        assert "flowchart" in frame_48["caption"].lower()

    def test_gallery_captions_fallback_to_ocr(self):
        """Frames not covered by vision should use OCR text for captions."""
        result = assemble_response(
            triage=self._triage_with_overview(),
            extraction=self._extraction(),
            enrichment=None,
            synthesis=None,
            frames=_ALL_FRAMES,
            gallery_frames=_GALLERY_FRAMES,
            all_frames=_ALL_FRAMES,
            frame_descriptions=_FRAME_DESCRIPTIONS,
        )

        gallery_tab = next(t for t in result["tabs"] if t["id"] == "frames-gallery")
        images = gallery_tab["props"]["frames"]

        # Frame at 35s has no vision description but has OCR text
        frame_35 = next(img for img in images if img["timestamp"] == 35.0)
        assert "Edge cases" in frame_35["caption"]

    def test_gallery_without_frame_descriptions(self):
        """Without frame_descriptions, gallery uses OCR or generic captions."""
        result = assemble_response(
            triage=self._triage_with_overview(),
            extraction=self._extraction(),
            enrichment=None,
            synthesis=None,
            frames=_ALL_FRAMES,
            gallery_frames=_GALLERY_FRAMES,
            all_frames=_ALL_FRAMES,
            frame_descriptions=None,
        )

        gallery_tab = next(t for t in result["tabs"] if t["id"] == "frames-gallery")
        images = gallery_tab["props"]["frames"]

        # Without vision, frame_12 should use OCR text
        frame_12 = next(img for img in images if img["timestamp"] == 12.0)
        assert "def binary_search" in frame_12["caption"]

        # Frame_35 should use OCR text
        frame_35 = next(img for img in images if img["timestamp"] == 35.0)
        assert "Edge cases" in frame_35["caption"]


# ─────────────────────────────────────────────────────────────────────────────
# Integration: find_description_for_frame
# ─────────────────────────────────────────────────────────────────────────────


class TestFindDescriptionForFrame:
    """Test timestamp-based frame description matching."""

    def test_exact_match(self):
        frame = {"timestamp": 12.0}
        desc = find_description_for_frame(frame, _FRAME_DESCRIPTIONS)
        assert desc is not None
        assert desc["scene_type"] == "code"

    def test_close_match_within_tolerance(self):
        frame = {"timestamp": 14.0}  # 2s away from 12.0
        desc = find_description_for_frame(frame, _FRAME_DESCRIPTIONS, tolerance=5.0)
        assert desc is not None
        assert desc["timestamp_sec"] == 12.0

    def test_no_match_outside_tolerance(self):
        frame = {"timestamp": 30.0}  # Far from any description
        desc = find_description_for_frame(frame, _FRAME_DESCRIPTIONS, tolerance=3.0)
        assert desc is None

    def test_empty_descriptions(self):
        frame = {"timestamp": 12.0}
        assert find_description_for_frame(frame, []) is None

    def test_picks_nearest(self):
        frame = {"timestamp": 50.0}  # Closest to 48.0 (diagram)
        desc = find_description_for_frame(frame, _FRAME_DESCRIPTIONS, tolerance=5.0)
        assert desc is not None
        assert desc["scene_type"] == "diagram"


# ─────────────────────────────────────────────────────────────────────────────
# Integration: vision → injection → assembly full chain
# ─────────────────────────────────────────────────────────────────────────────


class TestFullChainIntegration:
    """End-to-end test: vision analysis → annotations block → assembly."""

    async def test_full_pipeline_chain(self):
        """Simulate the complete frame-intelligence data flow."""
        # Step 1: Vision analysis (mock LLM returns structured response)
        vision_response = json.dumps(
            [
                {
                    "frame_index": 0,
                    "scene_type": "code",
                    "content": "Python binary search implementation",
                    "text_visible": "def binary_search(arr, target):",
                    "educational_value": "Shows the actual code",
                },
                {
                    "frame_index": 1,
                    "scene_type": "diagram",
                    "content": "Search space halving visualization",
                    "text_visible": "O(log n)",
                    "educational_value": "Illustrates the algorithm's efficiency",
                },
            ]
        )

        frame_metadata = [
            {
                "index": 0,
                "timestamp_sec": 12.0,
                "s3_url": "https://s3/f12.jpg",
                "original_index": 12,
            },
            {
                "index": 1,
                "timestamp_sec": 48.0,
                "s3_url": "https://s3/f48.jpg",
                "original_index": 48,
            },
        ]

        # Parse vision response (unit operation)
        descriptions = parse_vision_response(vision_response, frame_metadata)
        assert len(descriptions) == 2
        assert descriptions[0]["scene_type"] == "code"
        assert descriptions[1]["scene_type"] == "diagram"

        # Step 2: Render the <visual_annotations> block extraction reads
        lines = _entry_lines(render_visual_annotations(descriptions, _ALL_FRAMES))

        assert {
            "[0:12] Python binary search implementation | def binary_search(arr, target):",
            "[0:48] Search space halving visualization | O(log n)",
            "[0:08] | binary_search.py — VS Code",
        } <= set(lines)

        # Step 3: Assembly with frame_descriptions produces descriptive captions
        assembled = assemble_response(
            triage={
                "contentTags": ["tech"],
                "primaryTag": "tech",
                "userGoal": "Learn binary search",
                "modifiers": [],
                "tabs": [
                    {"id": "code", "label": "Code", "emoji": "💻", "dataSource": "tech.snippets"},
                ],
            },
            extraction={
                "tech": {
                    "snippets": [
                        {
                            "language": "python",
                            "code": "def binary_search():",
                            "explanation": "Main",
                        }
                    ],
                },
            },
            enrichment=None,
            synthesis={"tldr": "Binary search tutorial"},
            frames=_ALL_FRAMES,
            gallery_frames=_GALLERY_FRAMES,
            all_frames=_ALL_FRAMES,
            frame_descriptions=descriptions,
        )

        # Gallery tab should exist with descriptive captions
        gallery = next((t for t in assembled["tabs"] if t["id"] == "frames-gallery"), None)
        assert gallery is not None

        captions = [img["caption"] for img in gallery["props"]["frames"]]
        # At least one caption should come from vision (not generic "Moment at")
        vision_captions = [c for c in captions if "Moment at" not in c]
        assert len(vision_captions) >= 1, f"No vision captions found: {captions}"

    async def test_pipeline_degrades_gracefully_without_vision(self):
        """When vision returns nothing, pipeline still works with OCR only."""
        # No vision descriptions: the block still carries the OCR-only entries
        assert annotation_entries(render_visual_annotations([], _ALL_FRAMES))

        # Assembly without frame_descriptions
        assembled = assemble_response(
            triage={
                "contentTags": ["learning"],
                "primaryTag": "learning",
                "userGoal": "Learn",
                "modifiers": [],
                "tabs": [],
            },
            extraction={"learning": {"keyPoints": []}},
            enrichment=None,
            synthesis=None,
            frames=_ALL_FRAMES,
            gallery_frames=_GALLERY_FRAMES,
            all_frames=_ALL_FRAMES,
            frame_descriptions=None,
        )

        # Gallery should still exist with OCR/generic captions
        gallery = next((t for t in assembled["tabs"] if t["id"] == "frames-gallery"), None)
        assert gallery is not None
        captions = [img["caption"] for img in gallery["props"]["frames"]]
        # All captions should be OCR or generic (no vision)
        assert all("VISUAL" not in c for c in captions)

    async def test_feature_flag_disabled_produces_no_descriptions(self):
        """Simulate FRAME_VISION_ENABLED=false: no frame_descriptions, pipeline unaffected."""
        # With no descriptions and no OCR frames there is no annotations block
        empty_frames = [{"index": 0, "timestamp": 10.0}]

        assert render_visual_annotations([], empty_frames) == ""
