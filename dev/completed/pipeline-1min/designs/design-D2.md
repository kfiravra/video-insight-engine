# D2 design — 1d.3 synthesis trimmed (DESIGN mode, no repo edits)

Read: agent-rules, briefs header + D2/C1/C3/D1/D4, context D1–D25, plan 1d table, brief §1b/§1d + B.3/B.4/D,
CODE-MAP §1 row 5 + §2 1d, backend-python SKILL. Code re-read at HEAD eabe954 + the in-flight wiring file
`src/routes/pipeline_orchestration.py` (untracked; still has Phase 2.5 and synthesis ∥ enrichment).

## Facts that shape the design
- Today: `synthesize()` (fast model, 4 fields, `extraction_summary[:4000]`, max_tokens 8192, 30 s, 2 retries);
  phase = hierarchical chapter input when > 5 chapters, "already populated" skip branch, one event.
- `ctx.synthesis_dict` readers: assembly meta (`core.py:1347-1351`), overview assembler (`assemblers_learning
  .assemble_overview` reads `_synthesis.tldr/masterSummary/keyTakeaways`), `build_fallback_candidates`,
  Mongo `pipeline.synthesis`, enrichment's empty-extraction fallback (D1 rewrites), C3's retry (being removed).
- `MemoryResult.tldr` / `.takeaways` can be empty independently (repair → "" / []). Brief 1b.3: on memory
  failure tldr/takeaways come "from synthesis as today" → synthesis needs a hero fallback mode.
- Moment fill runs INSIDE `phases/assembly.py` on assembled moment tabs, after tab emission. The overview tab
  needs masterSummary. So "synthesis ∥ moment fill" = synthesis after `assemble_response`, overview + meta
  patched when it lands (phase-3 "overview/meta last"). core.py stays untouched.
- Reducer (530c3ac) merges emissions; empty fields never overwrite. Registry guard (da3a61d): a changed
  placeholder set makes the loader render the disk file until re-registered.

## File-by-file
### `src/prompts/synthesis.txt` (rewrite)
- `<role>` closing summary; `<video>` title / channel / duration / content type (kept: video_memory has no
  title); `{video_context}` placed BARE (the video_memory block carries its own `<video_memory>` tags — no
  double wrapper); `<extracted_content>{extraction_summary}</extracted_content>`; `<tabs>{tab_labels}</tabs>`.
- Instructions: tone-matching line; "use only facts from video_memory + extracted content";
  masterSummary 2–3 paragraphs, start with what makes THIS video worth watching, follow the outline order,
  end by pointing to the listed tabs by label, never a tab not listed; seoDescription ≤ 160 chars.
- `{hero_fields}`: "" normally; in fallback mode the tldr (≤ 150, angle not topic) + keyTakeaways (3–5,
  specific number/name/measurement) lines (wording aligned with memory.txt `<summary_rules>`).
- Example JSON with masterSummary + seoDescription only, single braces (memory.txt style; today's `{{ }}`
  reach the model literally because rendering uses `.replace`). Last line: return only JSON with `{output_keys}`.
- New placeholders ⇒ registry guard serves disk until `register_prompts.py --commit` (phase end, both projects).

### `src/services/pipeline/synthesis.py` (service, ~200 lines)
- Constants: `SYNTHESIS_MAX_TOKENS = 1200` (was 8192), `SYNTHESIS_TIMEOUT_S = 20.0` (was 30),
  `SYNTHESIS_MAX_RETRIES = 1` (was 2; same as plan/memory), `EXTRACTION_SUMMARY_MAX_CHARS = 6000`,
  `SEO_MAX_CHARS = 160`.
- `compact_extraction(extraction, max_chars=6000) -> str` — the "compact final extraction": recursive prune
  (drop None/""/[]/{}; whitespace-collapse strings and cut at 200 chars on a word boundary; keep numbers/bools;
  lists capped at N with a trailing `"+k more"`), N stepped 8 → 5 → 3 → 1 until the compact JSON
  (`ensure_ascii=False`) fits; last resort `truncate_json_safely`. Every domain and field stays represented
  (today's `[:4000]` slice keeps only the head of the first domain). Deterministic.
- `format_tab_labels(labels) -> str` ("- 🛒 14 Ingredients" lines, "(none)" when empty).
- Internal frozen `SynthesisInput` (Pydantic, module-local) + `build_synthesis_prompt(request) -> str`
  (ENGLISH_OUTPUT_DIRECTIVE first as today; title/channel via `sanitize_for_prompt`; video_context empty →
  `<video_memory>\nNot available\n</video_memory>`).
- `parse_synthesis_output(data: object) -> SynthesisResult` (the validator): masterSummary must be a
  non-empty string else `ValueError`; seoDescription collapsed + word-cut to 160; tldr via
  `memory.repair_tldr`, keyTakeaways via `memory.repair_takeaways` (same 150-char / 3–5 rules as memory);
  kept whenever present (harmless, merge prefers memory), wrong types → defaults.
- `synthesize(llm_service, title, channel, duration, output_type, extraction_summary, video_context="", *,
  tab_labels=(), hero_fallback=False)` — positional part UNCHANGED (see Q2); `extraction_summary` capped at
  `EXTRACTION_SUMMARY_MAX_CHARS`; raises ValueError on no response / unparsable / no masterSummary (as today).
- `build_synthesis_dict(memory, result) -> dict[str, Any]` — the meta-shaped superset, key order as today
  (`tldr, keyTakeaways, masterSummary, seoDescription`): tldr/keyTakeaways = memory's when non-empty, else
  result's; masterSummary/seoDescription = result's; `{}` when all four are empty (today's failure value).
  `build_synthesis_dict(ctx.memory, None)` is the pre-assembly seed (memory hero → meta + overview).

### `src/services/pipeline/phases/synthesis.py` (~130 lines)
- `run_phase_synthesis(ctx) -> AsyncGenerator[str, None]` — SAME signature (the function D4 calls, alone or
  inside `run_parallel_phases([...moment fill phase...])`). Steps: input = `ctx.video_memory` +
  `compact_extraction(ctx.extraction_data)` + `_tab_labels(ctx)`; `hero_fallback = memory is None or
  not memory.tldr or not memory.takeaways`; call (failure → warning, result None, non-critical as today);
  `ctx.synthesis_dict = build_synthesis_dict(ctx.memory, result)`; `apply_synthesis_to_assembled(ctx)`;
  yield ONE `synthesis_complete` with all four keys (superset; memory's hero survives a failed call);
  log `pipeline.synthesis` + `tldr_source`/`takeaways_source` (memory|synthesis|none), `has_master_summary`.
- Removed: the "already populated" branch (it would SKIP synthesis whenever someone seeds
  `synthesis_dict` from memory) and `_build_hierarchical_synthesis_input` (the video_memory outline replaces
  the chapter list; compact extraction covers all fields).
- `_tab_labels(ctx)`: `ctx.assembled_tabs` when assembly ran (real surviving tabs), else plan tabs
  (`ctx.triage.tabs`); overview excluded; blanks/dupes dropped, order kept.
- `apply_synthesis_to_assembled(ctx)`: no-op while `ctx.assembled_meta is None` (old order keeps working).
  Else sets non-empty synthesis_dict values on `ctx.assembled_meta` (same 4 keys as core.py) and on the overview
  tab's `props.data` (masterSummary; tldr + subtitle; keyTakeaways — the `assemble_overview` mapping), in
  place, so the later save / Redis / Qdrant / translation see the final values. Parity test guards drift.

### `src/models/pipeline_types.py` — `SynthesisResult` only
`master_summary: str`, `seo_description: str = ""`, `tldr: str = ""`, `key_takeaways: list[str] =
Field(default_factory=list, alias="keyTakeaways")`. Fixtures with all 4 fields still validate
(test_llm_response_fixtures), `model_dump(by_alias=True)` keys unchanged.

## Interfaces
- Need (fixed): `ctx.memory: MemoryResult | None`, `ctx.video_memory: str` (wiring agent; not in context.py
  yet — re-read at GO), `ctx.assembled_tabs/assembled_meta` (exist), `memory.repair_tldr/repair_takeaways`.
- Provide to D4: `run_phase_synthesis(ctx)`, `build_synthesis_dict(memory, result)`. Recommended wiring in
  `phases/assembly.py`: seed `ctx.synthesis_dict = build_synthesis_dict(ctx.memory, None)` before
  `assemble_response` → emit non-overview, non-moment tabs → `run_parallel_phases([run_phase_synthesis,
  <moment-fill phase>], ctx)` → emit overview + moment tabs (or emit overview early and re-emit — D4's call)
  → complete → save. Then no synthesis wait before the first tab; done ≈ max(synthesis, moment fill).
- To D1: enrichment must not use `synthesis_dict` as context (in the new order it runs before synthesis).
- To wiring: if the memory-done emission also seeds `synthesis_dict`, use `build_synthesis_dict(memory, None)`.

## Tests (targeted; `.venv/bin/python -m pytest tests/test_synthesis.py tests/test_phase_synthesis.py -q`)
`tests/test_synthesis.py` (rewrite, mine):
- prompt: "should request only masterSummary and seoDescription when hero_fallback is false"; "should add
  the tldr and keyTakeaways rules when hero_fallback is true"; "should place the video_memory block verbatim";
  "should list the tab labels"; null channel → "Unknown"; null duration → "unknown"; no placeholder left.
- validator: "should raise when masterSummary is missing or blank"; "should cut seoDescription to 160 chars at
  a word boundary"; "should keep tldr/keyTakeaways when the model returns them"; "should default wrong types".
- compact: "should keep every domain and field when lists are capped"; "should drop empty values"; "should
  stay within max_chars"; "should be deterministic"; "should mark capped lists with +k more".
- call: unparsable → ValueError; retry exhausted → ValueError; max_tokens/timeout/retries passed.
`tests/test_phase_synthesis.py` (new, SimpleNamespace ctx like test_phase_assembly_streaming):
- meta shape: "should build the four meta keys with memory's tldr/takeaways and synthesis's summary";
  per-field fallback; `{}` when nothing; `assemble_response(synthesis=build_synthesis_dict(...))` meta keys/types.
- phase: "should emit one superset synthesis_complete"; "should request the hero fallback when memory is None or
  partial"; "should keep memory's hero when the LLM call fails"; "should call the LLM even when synthesis_dict is
  pre-seeded"; "should pass assembled tab labels without the overview when assembly ran, plan labels otherwise".
- parity: seed → `assemble_response` → phase patch gives the same meta + overview `props.data` as assembling
  with the final synthesis_dict.
Lint: ruff check/format + pyright@1.1.407 on the 4 touched src files.

## Risks
- Output-changing: synthesis prompt + inputs change; tldr/takeaways now from memory (Haiku via
  `LLM_EXTRACTION_MODEL`) — the hero text changes on every video. Gate golden covers it.
- Long video + memory failure: no outline and no chapter list in synthesis input (hierarchical mode gone).
- In the recommended order the overview tab lands after synthesis (~3 s, ∥ moment fill); FE already slots late
  tabs by `position` (moment tabs today). Translation reads the same dicts, patched in place before it runs.
- Smaller budgets (1,200 tok / 20 s / 1 retry): worst case 40 s before the overview/complete in order (b).
- Registry: deploy before re-registration serves disk (intended); re-register synthesis at phase end.
- Cost: +~900 input tok (video_memory + 6k-char compact), −~150 output tok on the fast model ≈ neutral.

## Open questions
1. Order: (b) synthesis after `assemble_response` ∥ moment fill (recommended) vs (a) before assembly as today
   (works unchanged, keeps ~3–5 s synthesis on the path to the first tab when no quiz runs). D4 decides; both work.
2. `synthesize` keeps its positional signature (7 params, pre-existing) so the D1-hot shared files
   `test_pipeline_integration.py` (4 synthesis tests) and `test_prompt_render_placeholders.py` need no edit
   and C3's in-flight caller keeps compiling. A `SynthesisInput`-arg refactor = phase-4 sweep. OK?
3. LLM budget change (8192/30 s/2 retries → 1200/20 s/1) — brief is silent; confirm.
4. If the wiring removes `ctx.video_dna_compact`, empty `video_memory` renders "Not available" (no DNA fallback).
