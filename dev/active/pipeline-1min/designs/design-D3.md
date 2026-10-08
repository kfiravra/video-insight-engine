# D3 — vision batching (1d.4) — DESIGN

Read: briefs header + D3, agent-rules, context D1–D25, plan 1d.4/C8/C14, brief §1d, A3/A18 (ans-vision-worker-box),
CODE-MAP 1d row, HEAD code (frame_analyzer 294 l., frames.py 216, scene_extractor 572, scene_detect 336, llm_retry,
llm_provider, prompt_builder, replay fake_llm/cassette), tests (test_frame_analyzer, test_scene_extractor).
Commits touching my files since c57c2a6: e18e948 (ladder → scene_detect.py), bf618ee/eabe954 (one 720p file), 8010731/b7bb9a5/d61ecc8.

## 1. Facts that shape the design
- Vision = ONE `complete_with_messages` call (`frame_analyzer.py:168`), labels `Frame {i} (at m:ss)`, parse maps local
  `frame_index` → `frame_metadata[i]`; out-of-range index today emits an entry with ts 0 / no s3_url (garbage annotation at 0:00).
- Downstream reads `timestamp_sec` + `original_index` only (reselect `frames.py:103`, `scene_frames`, assembly
  `find_description_for_frame`, gallery captions); `frame_index` is never read → per-batch local indices are safe.
- No retry, no fallback since 90576a0. `call_llm_with_retry` takes `UserContent = str | list[TextBlock]` → no image blocks,
  so vision needs its own 1-retry loop (reuse public `retry_after_seconds` from `utils/llm_retry.py`).
- "1024 upscale" = `scale={SCENE_DETECT_SCALE_WIDTH}:-2` in `scene_detect._scene_filter` (`:77`) and `_seek_frame` (`:209`);
  pass-1 is 360p (uC45 71.5 MB / 1,127 s ≈ 500 kbps = format 18), so detection JPEGs are 360p blown up to 1024×576 (792 tok).
- Frame scores are resolution-sensitive (Laplacian centre detail, Canny text density, Haar faces) → selection shifts a little.
- Replay keys LLM calls by feature + span + ordinal; HIGH cassettes hold ONE 40-item vision output → batching breaks
  jMq8/uC45 replays until D4 splits them (D4's brief). T1dQ (8 frames = 1 batch) keeps one call.

## 2. File-by-file changes
**NEW `services/summarizer/src/prompts/vision.txt`** (registry name `summarizer:vision`, auto from `_langfuse_name_for`).
Today's prompt text verbatim + an index→frame mapping paragraph (A18): "Each image is preceded by its label `Frame <n> (at
m:ss)`. `frame_index` is that `<n>`. Return exactly one object per labelled frame." **No `{placeholders}`** — the per-batch
count goes in a code-built closing text block ("This request has 8 frames: Frame 0 to Frame 7.") so the template stays
static across batches, registry-safe, and outside the placeholder inventory guard (no edit to the shared
`test_prompt_render_placeholders.py` that C1/D1/D2 also touch). Prompt wording otherwise unchanged (no output-shape change).

**`src/services/media/frame_analyzer.py`** (split into <50-line functions; ~370 lines):
- `PROMPT_PATH`; `load_vision_prompt()` → `load_prompt_text(PROMPT_PATH)` (registry-first, records prompt version on the
  trace; loaded once per analysis BEFORE the gather so every batch generation carries it). `VISION_ANALYSIS_PROMPT` deleted.
- Constants (no new settings — brief allows only two flags): `VISION_BATCH_SIZE = 8`, `VISION_MAX_PARALLEL = 5`.
- `plan_vision_batches(frames, batch_size) -> list[list[dict]]` (public, D4's replay split uses it): k = ceil(n/8);
  frames sorted by timestamp, frame i → batch i mod k (strided). Each batch is chronological, spans the whole video, and
  text-heavy clusters spread across batches → balanced output → stage wall ≈ even batch, not the worst one. 40 → 5×8,
  25 → 7/6/6/6, ≤ 8 → one batch. `FRAME_VISION_PARALLEL=false` → one batch with every frame (today's single call).
- `_batch_max_tokens(batch)` (D12/C14): Σ per-frame budget + 200 overhead; per frame 500 when `text_score >= 0.15`
  (frame_ocr's own text-heavy line; screens ~190 observed), else 250 (scenes/food ~85 observed). 8 plain frames = 2,200
  (≥ today's 2,000 floor), 8 screen frames = 4,200 (fixes kPN564Kol14's `finish=length` loss). Threshold re-checked on the
  measurement's native-res text_score distribution; constants adjusted there if needed. Ceiling only — cost = actual output.
- Per-batch timeout unchanged formula `max(FRAME_VISION_TIMEOUT, 4 s × frames)` (8 frames → 90 s) + outer +5 s.
- `_attempt_batch(...) -> list[dict] | None`: one call (span `frame_vision`, metadata `{frameCount, batch, batches,
  attempt, maxAttempts}` — `attempt` feeds `pipeline.timing` retries), parse; None on timeout / transient error / empty /
  unparseable / zero valid items. Timeouts recorded via `record_llm_failure` (provider never sees them, as in llm_retry).
- `_describe_batch(...)`: ≤ 2 attempts under the shared `asyncio.Semaphore(VISION_MAX_PARALLEL)`; pause =
  `retry_after_seconds(e)` else 1 s; a parse failure retries with 1.5× max_tokens (truncation case); non-retryable
  errors (4xx BadRequest/Auth/NotFound) fail the batch at once, mirroring 90576a0. Batch failure → `[]` + warning.
- `analyze_frames_with_vision(...)` (same signature/return): top-N by score (unchanged) → encode all images first
  (deterministic call order for the replay ordinals) → gather batches → concat, `frame_index` = global position,
  sorted by timestamp. **Partial success returns the successful batches** (A18 step 4; reselect floor tolerates it).
- `parse_vision_response`: stricter — items whose `frame_index` is non-int/out of range are dropped (today: kept with ts 0),
  duplicate indices keep the first; missing index keeps today's positional default.
- Model: comment rewritten — vision stays on Sonnet; `LLM_VISION_MODEL` unset (D5 → None) = caller's primary.

**`src/services/media/scene_detect.py`** (no upscale): both scale filters → `scale='trunc(min(iw,W)/2)*2':-2` (W =
`SCENE_DETECT_SCALE_WIDTH`, now a width CAP; even width kept for mjpeg; quoted like the existing `select='gt(…)'`).
360p sources stay 640×360 (≈314 tok/frame vs 792). Module docstring updated.

**`src/services/media/scene_extractor.py`** — only if 720p-after-hires wins (C8/D7): Step 6b refines all candidates
(`refine_selected_frames(candidates)`) BEFORE the hook, the hook sees 720p JPEGs, the later Step 7 refine is skipped for
those dicts (hires_count = refined ∩ selected); dedup/upload unchanged. If 360p-native wins: no change here (Step 6b already
runs before hires; it just receives native frames). Docstring line on pass-2 updated either way if wording drifts.

**`src/services/pipeline/phases/frames.py`**: no code change expected (the analyzer reads `FRAME_VISION_PARALLEL` itself —
one reader for both call sites); only the `_make_reselect_hook` docstring if the HIGH order changes. Keeps me off the
wiring agent's probe-await lines.

## 3. Interfaces
Needs: D5 — `FRAME_VISION_PARALLEL: bool = True` (all 8 touch points), `LLM_VISION_MODEL` default None (→ primary);
optional one-line config.py comment "SCENE_DETECT_SCALE_WIDTH = max width, never upscales" (D5's file).
Provides: `plan_vision_batches()` + "one call per batch, local labels 0..n-1, ordinal = batch order" for D4's replay split
(recorded single output: frame_index in score order → re-key per my plan). `ctx.frame_descriptions` shape unchanged
(C2's renderer input unchanged; now chronological and never carries ts-0 garbage entries).

## 4. Tests (targeted; `test_frame_analyzer.py`, `test_scene_detect.py`, `test_scene_extractor.py`, `test_phase_frames.py`)
- should map each batch's local frame_index back to its own frames when batches answer out of order (AC: index mapping).
- should send ≤ 8 images per call and cover every frame exactly once for 40 / 25 / 8 frames (strided, chronological).
- should never run more than VISION_MAX_PARALLEL calls at once (AC: semaphore — fake provider counts in-flight, limit
  patched to 2 with 5 batches; default 5 runs all 5 concurrently).
- should make one call with every frame when FRAME_VISION_PARALLEL is false.
- should return the other batches' descriptions when one batch fails both attempts.
- should retry a batch once after a timeout / transient error / unparseable reply, honouring retry-after; never twice;
  no retry on a 400.
- should give text-heavy frames the larger per-frame token budget; 8 plain frames ≥ 2,000.
- should load the vision prompt through the registry loader; vision.txt declares no placeholders and states the mapping.
- parser: should drop out-of-range and duplicate frame_index items (replaces `test_frame_index_out_of_range`).
- should render detection frames without upscaling (filter string; replaces `"scale=1024:-2"` assertions in
  test_scene_extractor `:363,393,573,717`).
- (if reorder) should refine every HIGH candidate before the reselect hook and not refine them twice.
Updated: `test_max_tokens_scales_with_batch` (30 frames → 4 batches), single-call assumptions in TestAnalyze*.
Lint: ruff check/format + pyright on touched files.

## 5. Measurement (C8/D7) — after the code stop, needs Kfir/coordinator OK on spend
Script in the scratchpad (not committed), `docker cp` into `vie-summarizer`, run with the container's env (proxy, keys);
llm_feature_var `dev:vision-measure` so ledger rows are separable. Per video: download pass-1 + 720p (record walls/bytes,
ffprobe the pass-1 resolution), detect + score at native width (also at 1024 for a free selection-overlap check),
`_sample_by_time(usable, 40)` candidates, then:
- Arm A 360p-native: batched vision on the detection JPEGs.
- Arm B 720p-after-hires: refine the same candidates from the 720p file (record 40-seek wall), batched vision on them.
Videos: **jMq8lEu-of0** (food HIGH, benchmark 2) + **uC45_4nnEAI** (unboxing HIGH, card text, benchmark 3) — both are
HIGH, where the order decision applies (STANDARD stays vision-after-upload on hi-res either way).
Runs: 2 videos × 2 arms = 4 vision passes ≈ 20 Sonnet calls. Cost est.: A ≈ $0.09–0.12/video (13–16k in, 3.5–5k out),
B ≈ $0.20–0.25/video (≈52k in) → **≈ $0.60 total** (cap: uC45 at 24 candidates if the coordinator wants ≤ $0.45).
Table: per arm — input/output tokens, cost, per-batch walls, stage wall (slowest batch), B's extra pre-vision wait
(720p ready + 40 seeks); quality — scene_type and visual_subject agreement, presenter-drop decision agreement (what
reselect acts on), frames with text, text_visible chars, plus my manual grade of the 10 most-divergent text frames per
video against the 720p image (exact / partial / wrong / missed). Also: native-vs-1024 selection overlap, text_score spread.
Pre-registered rule: pick **A** unless B gets clearly more text right (≥ 20 % more frames graded correct) or flips
presenter decisions B judges correctly in > 15 % of frames, AND B's extra wall ≤ 8 s. B raises HIGH vision cost
≈ $0.149 → ≈ $0.20 (breaks "cost ≤ baseline"); A drops it to ≈ $0.09–0.12. If B wins on quality only, propose arm C
(720p downscaled to 1024 ≈ today's token cost) as a follow-up measurement (~$0.15, asked first).

## 6. Cross-provider fallback for vision — recommendation: do NOT add now
Partial-success batches + 1 same-provider retry cover every observed failure (timeouts, one overloaded_error ever, zero
429s). A gpt-4o fallback would mix models inside one run's descriptions, cut against "vision stays on Sonnet", and add a
fallback-tagging path for a non-critical stage (a lost batch = 8 frames without captions; local selection still works).
Revisit only if gate-1 timing shows vision batch failures.

## 7. Risks
- Native-res scoring shifts frame selection (Laplacian/Canny/Haar are pixel-scale dependent) → changes which frames and
  descriptions ship (model-output change, stated). Quantified by the free overlap check; if overlap < ~70 %, report
  before keeping it (the fix would be in frame_scorer, not mine).
- Batches lose the cross-frame view (A18: small; the prompt asks for none). Chronological strided batches keep a
  whole-video span per call.
- Replay HIGH cassettes break until D4 splits them; coordinator should land D4's split with or right after this.
- `frame_descriptions` are now chronological and partial on batch failure → manifest persists partial sets (same as
  today's all-or-nothing, just finer).
- B (if chosen) makes HIGH vision wait for the 720p file; with t=0 downloads (1a.1) it is usually ready by score-done.

## 8. Open questions
1. STANDARD (8 frames) stays ONE call under N = 8, so T1dQhQAm8Tc's 36 s vision (on the frames-done → extraction path in
   phase 1) does not shrink. Batching STANDARD as 2 × 4 would cut ≈ 12 s for ≈ +$0.0015/run — allow it (k = ceil(n/4)
   for n ≤ 8)? Default: brief's N = 8.
2. Should `FRAME_VISION_PARALLEL=false` also restore the old 2,000-token floor / no retry? Design: no — the flag toggles
   batching only (prompt file, token budget, retry, no-upscale apply to both), so the rollback path keeps the fixes.
3. Measurement spend ≈ $0.60 (4 passes) vs the brief's $0.3–0.6 — OK, or cap uC45 at 24 candidates (≈ $0.45)?
4. A public `TRANSIENT_LLM_ERRORS` alias in `utils/llm_retry.py` (not my file) would avoid duplicating its error tuple in
   frame_analyzer; otherwise I keep a small local tuple. Prompt re-registration (`summarizer:vision`) at phase end.
