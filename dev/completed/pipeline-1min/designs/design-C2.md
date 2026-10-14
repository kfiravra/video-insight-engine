# design-C2 — 1c.2 visual annotations out of clean_text

Read at HEAD 5bb125c (orchestration now in `src/routes/pipeline_orchestration.py`). Facts:
- Today the annotations reach the LLMs two ways: Phase 2.5 (`pipeline_orchestration.py:186-206`)
  mutates `ctx.clean_text` (so faithfulness + Qdrant transcript chunks see them), and
  `build_prompt_transcript` (`phases/extraction.py:53-59`) injects them again into the marked prompt
  transcript. Both go through `scene_frames.inject_visual_context` (startMs bug → every annotation
  lands after the first segment, A17/A20).
- The S3 blob is ALREADY clean (`transcript_store.store` writes normalized segments, not `clean_text`).
- Plan/memory read `render_transcript(segments)`; the probe reads `ctx.clean_text` windows at
  transcript-ready (+ `strip_visual_annotations`). Without Phase 2.5 no transcript source is annotated.
- `process_scene_frames(clean_text=…)` → `enrich_transcript_with_ocr` is a dead path (frames.py never
  passes `clean_text`). Left for the 4.1 sweep (it touches frames.py / frame_ocr.py, not mine).

## Output format (the contract C1's `<visual_context_guide>` describes)
```
<visual_annotations>
[0:12] Python binary search function with low/high pointers | def binary_search(arr, target):
  low, high = 0, len(arr) - 1
[0:48] Diagram of the search space halving each step | O(log n)
[1:05] | Binary Search — Complexity
</visual_annotations>
```
- One entry per frame, chronological, absolute video time via `render.format_marker` (`[m:ss]`,
  `[h:mm:ss]` from one hour — the transcript markers' clock).
- Vision frame: caption = `content` (≤ 300 chars, whitespace collapsed); on-screen text =
  `text_visible`, else the OCR text of the same frame (`original_index` == `all_frames[i].index`),
  ≤ 200 chars. OCR-only frame (not described by vision, OCR ≥ 10 chars): empty caption → `[m:ss] | text`.
  Entry without text → `[m:ss] caption` (no ` | `). Limits/filters = today's (talking_head without
  `educational_value` skipped; OCR < 10 chars skipped).
- On-screen text KEEPS its line breaks (vision `text_visible` is an exact transcription — code
  indentation matters for tech.snippets; today's injection kept them too); continuation lines are
  indented 2 spaces, blank lines dropped. Every entry starts with a marker at column 0.
- No `{}`/`<>` sanitizing (would corrupt code like `<div>` / `{ }`); only the literal
  `</visual_annotations>` inside text is defused (`‹/visual_annotations›`). Contract for C1: insert the
  block by concatenation / as the LAST `.replace`, never `str.format`.
- Consecutive duplicates dropped (static slides): an entry whose text equals the previous entry's
  text (case/whitespace-insensitive) AND whose caption is empty or equal to the previous caption.
- No frames / nothing usable → `""` (C1 then omits `<visual_context_guide>`).

## File-by-file

### 1. NEW `src/services/pipeline/visual_annotations.py` (~140 lines)
- `@dataclass(frozen=True) VisualAnnotation {seconds: float, caption: str, text: str}` + `render() -> str`.
- `collect_visual_annotations(frame_descriptions, all_frames) -> list[VisualAnnotation]` — vision
  entries + OCR-only entries, stable sort by seconds, dedupe (helpers `_vision_entries`,
  `_ocr_entries`, `_drop_repeats`; each < 50 lines).
- `render_visual_annotations(frame_descriptions, all_frames) -> str` — THE call D4 makes at frames-done:
  `ctx.visual_annotations = render_visual_annotations(ctx.frame_descriptions, ctx.scene_frames_all)`.
- `annotation_entries(block: str) -> list[tuple[int, str]]` — parses a rendered block back into
  (seconds, entry text incl. continuation lines) using `render.MARKER_PATTERN` anchored at line start.
  Used by RAG; D4 may use `len(...)` for the frames-done log line.
- Optional (only if C1 wants per-batch slicing in 1c.3, else not added — no dead code):
  `slice_visual_annotations(block, start_s, end_s) -> str` (phase 3.7 needs it anyway).

### 2. `src/services/pipeline/scene_frames.py` (312 → ~140)
Delete `_build_vision_annotations`, `_build_ocr_annotations`, `inject_visual_context`, `_insert_with_*`
+ section header; docstring drops "transcript enrichment". `process_scene_frames` untouched.

### 3. `src/services/pipeline/phases/extraction.py` (my hunk only)
Remove the `inject_visual_context` import (line 32) and lines 53-59; docstring: "Visual annotations
are not part of this text — they travel as `ctx.visual_annotations` (1c.2)." C3 deletes other import
lines in the same block → I re-read and edit with anchors that exclude C3's lines (as C3 agreed).

### 4. Faithfulness — `src/routes/pipeline_faithfulness.py` (+ `src/services/pipeline/faithfulness.py`)
- New `_judge_source(ctx) -> str` = `clean_text` + `"\n\n"` + `ctx.visual_annotations` (when non-empty);
  `_launch_faithfulness_check` passes it as `transcript=`. Window selection for > 80k chars is unchanged
  (annotation lines become rankable windows at the end).
- PROPOSED (changes judge output — informational metric, D22): one sentence in `_JUDGE_PROMPT`:
  "The excerpt may end with a <visual_annotations> block (what the video shows on screen at [m:ss]);
  a claim supported there counts as supported." Reason: today the annotations were unlabeled
  transcript lines; a tagged block may be discounted by the "be strict" rule → artificial drop vs g0.

### 5. RAG — `src/services/vector/{qdrant_service.py,chunking.py,store.py}`
- `qdrant_service.py`: `SOURCE_VISUAL = "visual"`; docstrings list three sources. Point ids are already
  namespaced for any source ≠ transcript (`_point_id`) → no collision with transcript ids.
- `chunking.py`: `chunk_visual_entries(entries, max_chunk_chars=1000) -> list[dict]` — packs whole
  entries (never split) into chunks ≤ 1,000 chars (same budget as transcript chunks so scores stay
  comparable); each chunk `{text, start_char, end_char, start_time, end_time}` with start = first
  entry's seconds, end = last entry's seconds (→ payload `timestamp`/`end_timestamp`).
- `store.py`: `async store_visual_chunks(video_id: str, visual_annotations: str) -> None` — never
  raises; `QDRANT_ENABLED` gate; pre-delete `(video_id, "visual")` FIRST, even when the block is empty
  (a rerun without frames must not leave stale visual points); then parse → chunk → `embed_texts` →
  `store_chunks(..., language="en", source=SOURCE_VISUAL)`. Chunk text prefixed `"On screen:\n"`
  (the assistant formats every source the same way — without a label it would quote visuals as speech).
- Transcript chunks: no code change; they are clean because `ctx.clean_text` is never mutated again.

### 6. Tests (all targeted)
- NEW `tests/test_visual_annotations.py`: chronological across vision + OCR; OCR joined onto a vision
  line without `text_visible`; OCR-only line `[m:ss] | text`; no duplicate OCR for vision-covered frames;
  talking_head filter; short-OCR filter; code newlines kept + indented; caption collapsed; closing tag
  defused; consecutive repeats dropped; hour clock; empty → `""`; `annotation_entries` round-trip.
- NEW `tests/test_visual_annotations_isolation.py`: sentinel caption/OCR on ctx; after the frames-done
  call: `ctx.clean_text` unchanged; `build_prompt_transcript` has no sentinel (segments + metadata-only);
  plan input (`run_phase_plan` with `run_plan` patched) has no sentinel; memory + probe inputs — via the
  builders the wiring agent lands (`TierProbeInput.from_video`, the shared rendered transcript); if
  they don't exist at GO, assert on their sources and say so.
- NEW `tests/test_faithfulness_judge_input.py`: judge gets clean_text + block; exactly clean_text
  without annotations.
- `tests/test_vector_store.py` (+class), `tests/test_chunking.py` (+class), `tests/test_qdrant_service.py`
  (+1): visual chunks `source="visual"` with `timestamp`/`end_timestamp`; pre-delete before upsert and
  on empty block; disabled no-op; entries never split; visual vs transcript point ids differ.
- DELETE `tests/test_inject_visual_context.py`. PORT `tests/test_frame_intelligence_integration.py`
  (`TestVisualContextInjectionIntegration` + the inject steps in `TestFullChainIntegration`) to the
  renderer with the same fixtures. FLIP `test_extraction_prompt_transcript.py::
  test_should_keep_visual_annotations_when_frames_were_described` → "should leave … out".

## Needs from files I don't own (report, not edit)
1. `context.py` (wiring → D4): `visual_annotations: str = ""` field. C1 needs it too — whoever lands
   first; I can add the one line if the coordinator says so.
2. `pipeline_orchestration.py` (D4): replace the Phase 2.5 block with the render call at frames-done;
   drop or keep the `visual_inject` timing key (also in `run_timing.py:57-64`, replay
   `test_replay.py:25`, `cassette_timing.py:75`, cassettes).
3. `phases/assembly.py` (D4), inside `if settings.QDRANT_ENABLED:`, unconditionally:
   `asyncio.create_task(store_visual_chunks(ctx.youtube_id, ctx.visual_annotations), name=...)` +
   `_log_qdrant_error`. Assembly-level "transcript chunks clean / visual stored" test: I write it if the
   hunk exists at GO, else it goes to D4 with this spec.
4. C1: pass `ctx.visual_annotations` into `extract(...)` — that call lives in `phases/extraction.py`
   (C2/C3 file, not C1's): I add the kwarg in my hunk once C1 names the parameter.
5. `frame_analyzer.format_visual_annotation` (+ its tests in `test_frame_analyzer.py`) becomes dead → D3
   deletes. `tier_probe.strip_visual_annotations` becomes a no-op guard → its owner / 4.1 sweep.
6. Docs (no owner): `RAG.md` + `SERVICE-ASSISTANT.md` (source `visual`); Phase 2.5 / `[VISUAL at]` in
   `summarizer-workflow.md`, `ARCHITECTURE.md`, `SERVICE-SUMMARIZER.md`, `API-REFERENCE.md`, briefings.

## Assistant (read-only check, D10)
Tolerant: payload `source` read as `str` with default `"transcript"` (`qdrant_repository.py:99`,
`rag.py:249`, `RAGSource.source: str`); no branching on source; `sources=None` (the web never sends
it) returns all sources → visual chunks are retrieved. Gap: `requests.py:100` types the filter as
`Literal["transcript","default_output"]` → a client cannot filter TO `visual` (422). Not a blocker.

## Ordering risk
Deleting `inject_visual_context` while Phase 2.5 still imports it (lazy import inside the run) makes
every run with frames fail. My scene_frames deletion and D4's Phase 2.5 hunk must land in the SAME
commit (hunk-wise staging) or D4's first. Between my build_prompt_transcript change and C1's placement
of the block, extraction has no visual facts — fine inside one PR group, no gate runs in between.

## Model-output changes (explicit)
Extraction: annotations move from inline (all after segment 1) to a tail block with real times, OCR
folded into vision lines, repeats dropped. Faithfulness gains the labeled block (+ optional sentence).
RAG gains `visual` points; transcript points lose annotation text.

## Open questions
1. Faithfulness prompt sentence — add (my recommendation) or keep the prompt byte-identical?
2. `"On screen:\n"` chunk label — OK?
3. Who adds the `context.py` field; my Phase-2.5 commit pairing with D4.
4. key_frames: stays in `prompt_builder.format_gallery_frames_for_extraction` (C1) — it is a gallery
   selection for frame-bearing components, not an annotation; suggestion to C1: same `[m:ss]` clock,
   and with annotations present it could shrink to "frames with images: 0:12, 0:48 …" (≈ 400 tokens
   of duplicate captions today). C1 decides.
5. Per-batch annotation slicing for chunked extraction in 1c.3 — C1 wants it (I add the slicer) or not.
