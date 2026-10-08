"""Frame captions + on-screen text as one ``<visual_annotations>`` prompt block (1c.2).

Vision descriptions and OCR render as a chronological block the extraction call
reads after the transcript. They are never written into ``clean_text``, so the
tier probe, plan, memory, the Qdrant transcript chunks and the S3 blob stay
speech-only. The same rendered block feeds the faithfulness judge and is indexed
in Qdrant as ``source="visual"`` chunks.

Format — one entry per frame, on the transcript markers' clock (absolute video
time, ``[m:ss]`` / ``[h:mm:ss]``)::

    <visual_annotations>
    [0:12] Python binary search function | def binary_search(arr, target):
      low, high = 0, len(arr) - 1
    [0:48] Diagram of the search space halving
    [1:05] | Binary Search — Complexity
    </visual_annotations>

* A described frame: caption = what vision saw; on-screen text = vision's
  transcription, else the frame's own OCR text.
* A frame vision did not describe: OCR text only, empty caption (``[m:ss] | text``).
* On-screen text keeps its line breaks (code indentation matters to extraction);
  continuation lines are indented so every entry starts with its marker.
* An entry repeating the previous entry's text with no new caption (a static
  slide sampled twice) is dropped.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any, NamedTuple

from src.services.transcript.render import MARKER_PATTERN, format_marker, marker_seconds

OPEN_TAG = "<visual_annotations>"
CLOSE_TAG = "</visual_annotations>"
CAPTION_MAX_CHARS = 300
TEXT_MAX_CHARS = 200
# Shorter OCR reads are noise (stray glyphs, watermarks), not on-screen text.
OCR_MIN_CHARS = 10
_CONTINUATION_INDENT = "  "
# A frame showing the closing tag would end the block early inside the prompt.
_DEFUSED_CLOSE_TAG = "‹/visual_annotations›"


@dataclass(frozen=True)
class VisualAnnotation:
    """One frame's entry: when it was shown, what it shows, the text on it."""

    seconds: float
    caption: str
    text: str

    def render(self) -> str:
        """``[m:ss] caption | text`` with continuation lines indented."""
        head = format_marker(self.seconds)
        if self.caption:
            head = f"{head} {self.caption}"
        if not self.text:
            return head
        first, *rest = self.text.split("\n")
        lines = [f"{head} | {first}", *(_CONTINUATION_INDENT + line for line in rest)]
        return "\n".join(lines)


class AnnotationEntry(NamedTuple):
    """A rendered entry parsed back from a block: its marker time and its full text."""

    seconds: int
    text: str


# ─── Text normalization ──────────────────────────────────────────────────────


def _clean_caption(raw: object) -> str:
    """One line, whitespace collapsed, capped."""
    caption = " ".join(str(raw or "").split())[:CAPTION_MAX_CHARS]
    return caption.replace(CLOSE_TAG, _DEFUSED_CLOSE_TAG)


def _clean_text(raw: object) -> str:
    """Capped, line breaks kept (trailing spaces and blank lines dropped)."""
    text = str(raw or "").strip()[:TEXT_MAX_CHARS]
    lines = [line.rstrip() for line in text.splitlines() if line.strip()]
    return "\n".join(lines).replace(CLOSE_TAG, _DEFUSED_CLOSE_TAG)


def _ocr_text(frame: Mapping[str, Any]) -> str:
    """The frame's OCR text, or ``""`` when too short to be real on-screen text."""
    raw = str(frame.get("ocr_text") or "").strip()
    return _clean_text(raw) if len(raw) >= OCR_MIN_CHARS else ""


def _norm(text: str) -> str:
    return " ".join(text.split()).casefold()


# ─── Collection ──────────────────────────────────────────────────────────────


def _is_filler(desc: Mapping[str, Any]) -> bool:
    """A talking head with no stated educational value adds nothing to extract."""
    return desc.get("scene_type") == "talking_head" and not desc.get("educational_value")


def _vision_entries(
    frame_descriptions: Sequence[Mapping[str, Any]],
    ocr_by_index: Mapping[int, str],
) -> tuple[list[VisualAnnotation], set[int]]:
    """Entries for described frames + the frame indices they cover."""
    entries: list[VisualAnnotation] = []
    covered: set[int] = set()
    for desc in frame_descriptions:
        if _is_filler(desc):
            continue
        index = desc.get("original_index")
        own_ocr = ocr_by_index.get(index, "") if index is not None else ""
        caption = _clean_caption(desc.get("content"))
        text = _clean_text(desc.get("text_visible")) or own_ocr
        if not caption and not text:
            continue
        seconds = float(desc.get("timestamp_sec") or 0)
        entries.append(VisualAnnotation(seconds=seconds, caption=caption, text=text))
        if index is not None:
            covered.add(index)
    return entries, covered


def _ocr_entries(
    all_frames: Sequence[Mapping[str, Any]], covered: set[int]
) -> list[VisualAnnotation]:
    """Text-only entries for frames vision did not describe."""
    entries: list[VisualAnnotation] = []
    for frame in all_frames:
        if frame.get("index") in covered:
            continue
        text = _ocr_text(frame)
        if not text:
            continue
        seconds = float(frame.get("timestamp") or 0)
        entries.append(VisualAnnotation(seconds=seconds, caption="", text=text))
    return entries


def _repeats(entry: VisualAnnotation, previous: VisualAnnotation) -> bool:
    """Same on-screen text as the previous entry and no new caption."""
    same_text = _norm(entry.text) == _norm(previous.text)
    no_new_caption = not entry.caption or _norm(entry.caption) == _norm(previous.caption)
    return same_text and no_new_caption


def _drop_repeats(entries: Sequence[VisualAnnotation]) -> list[VisualAnnotation]:
    kept: list[VisualAnnotation] = []
    for entry in entries:
        if kept and _repeats(entry, kept[-1]):
            continue
        kept.append(entry)
    return kept


def collect_visual_annotations(
    frame_descriptions: Sequence[Mapping[str, Any]],
    all_frames: Sequence[Mapping[str, Any]],
) -> list[VisualAnnotation]:
    """Vision + OCR entries in video order (vision first on equal times), repeats dropped.

    Args:
        frame_descriptions: ``ctx.frame_descriptions`` (``content``, ``text_visible``,
            ``timestamp_sec``, ``scene_type``, ``original_index`` …).
        all_frames: ``ctx.scene_frames_all`` (``index``, ``timestamp``, ``ocr_text``).
    """
    ocr_by_index = {
        frame["index"]: _ocr_text(frame) for frame in all_frames if frame.get("index") is not None
    }
    vision, covered = _vision_entries(frame_descriptions, ocr_by_index)
    ocr_only = _ocr_entries(all_frames, covered)
    return _drop_repeats(sorted(vision + ocr_only, key=lambda entry: entry.seconds))


# ─── Public API ──────────────────────────────────────────────────────────────


def render_visual_annotations(
    frame_descriptions: Sequence[Mapping[str, Any]],
    all_frames: Sequence[Mapping[str, Any]],
) -> str:
    """The ``<visual_annotations>`` block, or ``""`` when no frame has a caption or text.

    Called once at frames-done: ``ctx.visual_annotations =
    render_visual_annotations(ctx.frame_descriptions, ctx.scene_frames_all)``.
    """
    entries = collect_visual_annotations(frame_descriptions, all_frames)
    if not entries:
        return ""
    body = "\n".join(entry.render() for entry in entries)
    return f"{OPEN_TAG}\n{body}\n{CLOSE_TAG}"


def annotation_entries(block: str) -> list[AnnotationEntry]:
    """Parse a rendered block back into entries (continuation lines kept with their entry)."""
    entries: list[AnnotationEntry] = []
    for line in block.splitlines():
        if not line.strip() or line in (OPEN_TAG, CLOSE_TAG):
            continue
        marker = MARKER_PATTERN.match(line)
        if marker:
            entries.append(AnnotationEntry(marker_seconds(marker.group(0))[0], line))
        elif entries:
            last = entries[-1]
            entries[-1] = AnnotationEntry(last.seconds, f"{last.text}\n{line}")
    return entries
