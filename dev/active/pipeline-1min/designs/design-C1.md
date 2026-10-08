# design-C1 — extraction prompt (1c.4 → 1c.1 → 1c.3, fast-first removal)

Read: briefs-1c1d header + C1, agent-rules, context D1–D25, plan 1c rows + C2/C6, brief §1c + B.5 + App. D,
CODE-MAP §2 1c, A26 (ans-q26), commits 99bff3b / c311479 / da3a61d / 90576a0, design-C3, wiring-brief,
backend-python SKILL + ai-integration/testing resources. All 16 schemas, 8 examples, base_extraction.txt,
prompt_builder.py (492 l.), extractor.py (768 l.), phases/extraction.py, llm_messages.py, the tests below.

## Facts that shape the design (verified in code, 2026-10-08)
- `> UI` lines are NOT stripped for the LLM (brief says "already stripped"): `_load_schema` sends the file
  as-is; only `test_datasource_registry._schema_skeleton` drops `>` lines. Kept per brief; reported.
- Modifiers validate only when wrapped under their key (`domain_types._wrapped_block`), so narrative's
  "Merge this INTO the main domain data" loses keyMoments/quotes → "own key" is a real bug fix.
- Finance consumer confirmed: `assemble_budget` (`costs`, `savingTips`) for a `finance`/`finance.*` tab
  (bypasses the registry, `plan._bypasses_registry`), `_FIELD_EQUIVALENTS` cross-domain fallbacks
  (`savingTips`, `costs`), RAG `output_chunker` `savingTips`. Finance has no SCALING/`{duration_minutes}`.
- `{{…}}` in `<output_rules>` is never unescaped (`.replace` rendering) → the LLM sees `{{"tech": …}}` today.
- Examples: header change is wording-only (no placeholders) → the Langfuse copy WINS until re-registered
  (da3a61d guard fires only on placeholder-set changes). Schemas/base change placeholders → disk wins.
- TriageResult.tabs = plan tabs incl. normalized `brief {what, where, expect}` → no new interface for briefs.
- Replay cassettes: all 3 have ONE extraction call (no chunked batches); cache tokens are replayed from the
  cassette, not computed from the prompt (`fake_llm._build_response`).

## 1c.4 — schemas + examples (STOP 1)
`src/prompts/schemas/*.txt` (16):
- Delete every `SCALING (video is {duration_minutes} minutes):` block (15 files) → no `{duration_minutes}` left.
- tech: drop counts; fold "every function, command, and example" into the existing RULES "Include all code…".
- learning: COMPLETENESS → one line "Cover the full video chronologically — don't cluster points from the
  first 10 minutes." (drop "at least one keyPoint/concept per topic/chapter").
- travel: drop "Minimum: 1 day with spots, 5 packing items"; `> REQUIRED for weight` → "Fill `weight` (kg)
  only when the video states it; omit it otherwise (never 0)".
- narrative: "Merge this INTO the main domain data." → "Return it under its own `"narrative"` key, next to
  the domain keys."; finance: same own-key sentence replaces "it adds financial data alongside…" (wording
  only; schema otherwise unchanged).
- Keep: JSON skeleton, inline `// Example` blocks, `> UI` / `> GROUPING` lines, RULES, COMMON MISTAKES,
  gaming THINKING GUIDE + unboxing note, food INGREDIENT AMOUNT RULES.
`src/prompts/examples/*.txt` (8): line 1 → "Here is one high-quality example of <d> extraction output.
Match this specificity and field completeness; counts come from your brief."
`prompt_builder._load_domain_example`: missing file → `""` (no learning fallback, no "follow the schema…"
text); unsafe tag → `""`. The caller drops the whole `<extraction_example>` element when empty.
Tests (new `tests/test_extraction_schemas.py`): per schema — skeleton JSON parses (`>` lines dropped),
inline example parses, no `SCALING`/`{duration_minutes}`/`Minimum:`/`Merge this INTO`, RULES present,
COMMON MISTAKES kept where it was; travel weight wording; example selection: 8 domains → their file,
gaming/language/science/podcast/news/sport → `""`; each example's first line is the new header.
Update `test_prompt_builder` fallback tests. Output-changing: yes (prompt text).

## 1c.1 — base_extraction.txt (STOP 2)
One file, three sections split by two literal markers (`<video>` and `<your_job>`), so it stays ONE registry
entry and the da3a61d guard covers the whole layout. Sections:
1. System (rules only, zero placeholders except `{quality_rules}` = static file): ENGLISH directive (code) +
   `<role>` (minus "Fill all fields — empty fields mean dead UI") + `<instructions>` (component map; "read
   `<video_memory>` after the transcript"; "`<your_job>` names what to emit and the tabs you serve — their
   briefs say what/where/how many; never exceed a cap"; "`[m:ss]` markers are absolute video time;
   timestamps you output are whole seconds from the video start") + `<quality_rules>` + `<voice>` +
   `<output_rules>` (wrap each domain AND modifier under its key; single domain without modifier may be
   flat; single braces; "An empty array is correct when the video has no such content — never pad").
2. Cached head: `<video>` Title `{title}` · Length `{duration}` (m:ss via `render.format_marker`)
   `</video>` `<transcript>{transcript}</transcript>` `{video_memory}` (raw `ctx.video_memory`, carries its
   own tags; `"<video_memory>\n(not available)\n</video_memory>"`-style empty fallback).
3. Tail: `<your_job>{batch_context}Emit: {emit_domains} — the fields of the schemas below.` + "Tabs you
   serve:" `{tabs_to_serve}` + `{content_emphasis}` + "An empty array is correct when the video has no
   such content." `</your_job>`, `<schema>{domain_schemas}</schema>`, `<extraction_example
   domain="{primary_tag}">` "Match its specificity, not its count." `{domain_example}`, then
   `<visual_context_guide>` (new wording: lines are `[m:ss] caption | on-screen text` on the transcript's
   clock; screen wins on conflict; code tutorials: on-screen text carries the code) + `{visual_annotations}`
   + `<key_frames>` (today's wording) `{frame_context}`.
Removed: `<critical_rule>`, `<completeness>`, `<detail_level>` (duration scaling — deviation, same reason as
completeness), `{user_goal}` (already the `goal:` line of video_memory), `{duration_minutes}`, `{tab_goals}`.
Optional elements (`<extraction_example>`, `<visual_context_guide>`, `<key_frames>`) are removed whole by a
helper when their content is empty — wording stays in the registry-editable file.
`<tabs_to_serve>` renderer `render_tabs_to_serve(tabs)` (replaces `build_tab_goals`), one line per tab:
`- 🛒 14 Ingredients — checklist ← food.ingredients — what: … — where 1:10–2:40, 14:00–14:30 — expect ~14, cap 30`
(empty brief → `what: <goal>`; no where/expect segments when empty; cap from `data_source(path)`, object kind
→ "one object"; `frames`/overview/no dataSource → no ← segment, no cap; label/what via `sanitize_for_prompt`).
New module `src/services/pipeline/extraction_prompt.py` (prompt_builder is at 492 lines): `ExtractionPrompt`
(frozen dataclass: `system`, `head` with `{transcript}` late-bound, `tail` with `{batch_context}` late-bound;
`user_blocks(transcript, batch_context) -> list[TextBlock]` = `[text_block(head, cache=True),
text_block(tail)]`), `build_extraction_prompt(tags, modifiers, quality_rules, *, title, duration_seconds,
tabs, video_memory, visual_annotations, frame_context) -> ExtractionPrompt` (≤ 5 params via a small
`ExtractionPromptInput` dataclass), `render_tabs_to_serve`, `_drop_empty_element`, `_split_sections`
(markers missing — e.g. an old registry copy — → system "", whole text as ONE uncached user block + warning).
prompt_builder.py keeps the loader, `_load_schema`, `_load_domain_example`, `_EMPHASIS`/
`get_content_emphasis`, `format_gallery_frames_for_extraction` (clock → `format_marker` for h:mm:ss);
deletes `_build_base_template`, `build_extraction_prompt`, `build_extraction_template`, `build_tab_goals`,
`get_detail_level`. Tests: render tests (sections, briefs line incl. empty brief + object cap, optional
elements present/absent, annotations after transcript, no `[VISUAL` wording); `test_prompt_render_placeholders`
extraction class rewritten (every declared placeholder filled; late-bound = `{transcript, batch_context}`
only); `test_prompt_builder` trimmed to what stays. Output-changing: yes.

## 1c.3 — caller adoption + fast-first removal (STOP 3)
`extractor.py`: `extract(..., video_context, frame_context, visual_annotations="")` builds one
`ExtractionPrompt`; `_single_/_overflow_/_run_batch_extraction` call
`call_llm_with_retry(llm, prompt.user_blocks(text, batch_context), system_prompt=prompt.system, …)` — no
`cache_static`, no `use_fast_model`. Chunked: batch transcript (with `=== CHAPTER` headers) in the cached head,
`_build_batch_context` moves to the tail and is reworded (no reference to the removed density/"no empty
array" rules: "You see part N of M (m:ss–m:ss). Extract only what this part contains; other parts are merged
in. An empty array is correct for content outside this part."). Delete `_format_prompt`,
`_split_prompt_for_caching`, `EXTRACTION_USE_FAST_FIRST` reads (`:219-221`), every `use_fast_model` kwarg,
and — only after C3's `_attempt_synthesis_fed_retry` removal has landed (design-C3 order) —
`extra_instruction` + `force_primary_model` (`<retry_guidance>`). extractor.py shrinks ~40 lines (still > 500:
pre-existing; splitting chunked code out breaks ~30 test patches of `extractor.call_llm_with_retry` →
propose deferring to phase 3, which rewrites this module).
Tests: `tests/test_extraction_cache_layout.py` — (a) system text sha256 identical across two videos with
different title/duration/tags/modifiers/tabs/memory/transcript/annotations; (b) system contains none of
those per-video strings; (c) head is byte-identical across two calls of one run with different tails and is
the only block with `cache_control`; (d) chunked: each batch's head holds only its slice, the tail holds its
batch context; (e) simulated Anthropic prefix cache (fake `acompletion` hashing system + blocks up to the
breakpoint) → second call over the same head reports `cache_read_input_tokens > 0` in `pipeline.timing`.
Update/delete: `test_chunked_extraction` (fast-first class `:616-710`, `_format_prompt`/
`_split_prompt_for_caching` classes, batch-context wording asserts), `test_extractor` (`_format_prompt`,
`build_extraction_template` mocks → `build_extraction_prompt`), `test_extraction_prompt_transcript` if needed.

## Interfaces
Need: `ctx.video_memory` (wiring agent puts it in extract's existing `video_context=` kwarg — no rename);
`ctx.visual_annotations` from C2 passed as `extract(visual_annotations=…)` by the phases/extraction.py owner.
Provide: `extract(..., visual_annotations: str = "")`; `ExtractionPrompt` for phase 3 groups (one head,
many tails). Proposal to C2: key_frames stays C1's (`format_gallery_frames_for_extraction`, `frame_context`
kwarg — no move); C2's block = annotations only, line format `[m:ss] caption | on-screen text` (my guide
text depends on it — confirm).

## Risks
- Prompt thinning (no floors/completeness/detail level) → fewer items; mitigated by brief `expect` + golden
  min-items assertions; long-video coverage loses the "every chapter represented" nudge (learning keeps the
  chronological line). Gate 1 decides.
- Registry: examples keep the OLD header in any env until `register_prompts.py --commit`; gate-1 dev runs
  need `PROMPT_SOURCE=disk` (dev .env has it) — prod gets the new header only after re-registration.
- Cache: new cached block = system (~1.1k Claude tok) + head (transcript + ~0.35k memory); on Haiku it caches
  only when ≥ 4,096 → transcript ≳ 2.6k tokens (~15 min speech; A26 assumed 1.6k rules + 1k memory = 7 min).
  Below the floor nothing is written (no premium).
- Phase 1 has NO cache reads by construction: one call per batch, every batch's head differs. Today's single
  call also writes ~5k tokens and never reads → cost ≈ unchanged (+~$0.0002/run on Haiku).

## Open questions (need the coordinator)
1. AC "cache-read > 0 on batch 2 in replay": impossible as written (batch heads differ; cassettes have no
   batch 2; replay replays recorded usage). Proposal: test (e) above + a gate note; reads land in phase 3
   (groups share a chunk's head). Alternative only if you want it: D4 adds an opt-in prefix-cache simulation
   to `tests/replay/fake_llm.py`.
2. Delete `scripts/eval_extraction_models.py` + `tests/test_eval_extraction_models.py` with fast-first
   (it exists only to A/B that flag and passes `force_primary_model`) — assign to C1?
3. Who adds `visual_annotations=ctx.visual_annotations` to the `extract()` call in phases/extraction.py
   (wiring agent / C2 / C3)? One line; extract's default `""` keeps it safe either way.
4. Dropping `<detail_level>` and keeping `<content_emphasis>` (tail) — OK?
5. Brief wording "`> UI` lines (already stripped for the LLM)" is false; keep sending them (brief: keep)?

## Suggested commits
`p1c.4 feat(summarizer): extraction schemas without SCALING/floors, modifiers under their own key, examples count-free and no learning fallback`
`p1c.1 feat(summarizer): base_extraction = rules / transcript+memory / job sections with briefs, caps and conditional visual blocks`
`p1c.3 perf(summarizer): extraction sends rules as system and caches transcript+memory as a user block; fast-first removed`
