# Phase 3 design — the split (v9)

Design agent, 2026-10-12, on HEAD 41501dd (`feat/pipeline-1min`, phase 2 on prod). Binding inputs: context D1–D28,
tasks.md Phase 3 rows + Kfir's sub-items (3.2 code evidence, 3.4 three cache blocks + warm-first), plan §Phase 3,
brief §2/§Phase 3/App. A–C, Kfir's phase-3 rules (2026-10-12: `EXTRACTION_PARALLEL=false` = today's single call,
byte-identical; order 3.1∥3.2∥3.3 → 3.4 → 3.5 → 3.6 → 3.7∥3.8 → 3.9; outside the nine tasks → g3 question).
Every anchor below was opened on HEAD. Prefixes: `S/` = `services/summarizer/src/`, `P/` = `S/services/pipeline/`,
`T/` = `services/summarizer/tests/`, `R/` = `packages/shared/src/config/domains.json`.

## 1. Current state (post-phase-2)

**Orchestration** (`S/routes/pipeline_orchestration.py`): `_run_phases_in_order` 198–298 = metadata 219–222 →
`_run_phase_two` 163–195 (group `[then_provisional_moments(run_phase_text), run_phase_frames]` via
`run_parallel_phases` `P/pipeline_helpers.py:286`; a member's exception cancels the others) → `_render_visual_annotations`
149–160 at step `visual_inject` 229–233 → extraction 239 (`run_parallel_phases([run_phase_extraction])` for heartbeats)
→ faithfulness spawn 245–248 (`S/routes/pipeline_faithfulness.py:45`, judge source = `clean_text` + annotations) →
`run_phase_assembly` 254 → translation 261–289. **Extraction waits for frames-done** (the whole phase-two group).
- Text branch `P/phases/text.py:51-64`: transcript → `ctx.transcript_ready` → probe → garbage-caption swap →
  `ctx.prompt_transcript` (rendered once, 60) → `run_parallel_phases([run_phase_plan, run_phase_memory])` 61 →
  `ctx.video_memory = render_video_memory(plan, memory)` 64. Wrapper `P/phases/moments.py:103`
  (`then_provisional_moments`) emits the provisional Moments `tab_ready` right after.
- Plan `P/phases/triage.py:90-134`: `run_plan` → `_store_plan_result` 56 (ctx.plan_result/triage/triage_dict) →
  **`triage_complete` + `meta` yielded at plan end 119–120, before memory lands**.
- Plan parse `P/plan.py`: `_normalize_plan_data` 251–284 = registry check `_validate_data_sources` 199 → primaryTag →
  **plan-time guard `reconcile_plan_tabs`** 275 (`P/plan_reconcile.py:119-170`, hotfix 2.1: `inactive_domain` OR the
  PLAN's evidence false → re-point same-component sibling `_replacement` 67, else drop; moment_track kept) →
  `_enforce_domain_policy` 279 → fallback tabs when empty 282. Records → `PlanResult.reconcile`, persisted as
  `pipeline.reconcile` (`P/phases/assembly.py:286`). `_build_fallback_plan` 72–86 = `build_fallback_tabs(primary)`.
  `PLAN_CACHE_TTL="1h"` 56; `plan_user_content` 287–296 sends `[static (1h), video]`, no system prompt.
- Memory `P/memory.py` → `MemoryResult` (`S/models/memory_types.py:38`; evidence = answered keys only: "missing =
  no opinion, can't make a key doubly-false"). `merge_evidence` `P/video_memory.py:127-142`: true if either true, else
  false if any reader answered false (memory None → plan alone) — used only for the rendered `<video_memory>` line.

**Extraction prompt** (`P/extraction_prompt.py`, `S/prompts/base_extraction.txt`): `build_extraction_template` 243–260
fills one template; `_split_sections` 304–313 cuts it at `<video>`/`<your_job>`:
`system` = directive + role/instructions/`{quality_rules}`/voice/output_rules (base 1–48, ≈1.0k cl100k, no per-video
text, **plain string, no breakpoint**); `head` = `<video>` title/length + `<transcript>` + `{video_memory}` (50–59) →
user block 1 with `cache_control` **5m** (`user_blocks` 283–301, `S/services/llm_messages.py:48-66`); `tail` = `<your_job>`
(emit domains, tabs-to-serve lines with expect/cap, emphasis) + `<schema>` with every requested domain's FULL schema
(`_domain_schemas` 131–138) + `<extraction_example>` (primary only, 106–120) + visual guide + annotations + key_frames
(61–93) → **uncached**. Provider: list-of-blocks + `ttl` supported since 2.0 (`text_block(..., ttl="1h")`), 1h must
precede 5m, non-Anthropic strips markers (`prepare_for_model` 106). LiteLLM 1.91.0: `json_object` without schema is a
no-op on Haiku (`litellm/llms/anthropic/chat/transformation.py` `map_response_format_to_anthropic_tool` → None) → text.
- `P/extractor.py`: `extract` 140–226 → `_resolve_strategy` 80–137 (single < 5,333 words; chunked only if
  `batch_chapters` 393–438 gives >1 batch at `MAX_TOKENS_PER_BATCH` 50k / `MAX_MINUTES_PER_BATCH` 40; overflow; force
  split) → `_single_extraction` 303–337 (ONE call 312–322: max_tokens 16384, timeout 240, retries 2, JSON,
  `model_override=get_stage_model("extraction")`, span `extraction`) / `_chunked_extraction` 550–727.
  **`EXTRACTION_PARALLEL` today** (`S/config.py:120`) is read only at 597–601: chunked batches parallel vs one at a time;
  `EXTRACTION_PARALLEL_BATCHES` (128, =6) = that semaphore. All 3 cassettes take `single` → the flag is a no-op there.
- Phase `P/phases/extraction.py:235-328`: chapters only when duration > 900 s (`_split_chapters` 203 →
  `transcript_chunker.split_transcript_into_chapters` 313: creator chapters → description → memory outline /
  chapter_detect only if `needs_chapter_batching` 203) → `extract` → one `extraction_complete` SSE (the web merges
  `domainData` shallowly by domain key, `stream-event-processor.ts:135-143` → per-group copies would clobber) →
  `_record_validation` 44, coverage 70, quality 127 (`check_extraction_quality` = planned tabs' dataSources only).
- Merge `P/extraction_merger.py:22-59`: lists concat, **longest string wins** 72–75, numbers first-wins,
  `_IDENTITY_KEYS`/`_ORDER_KEYS` are **frozensets** (16, 19 → hash-seed-dependent pick), `_renumber_ordered_lists`
  renumbers any `number` key (shirt numbers).
- Validation `S/models/domain_types.py:1384-1413` → `S/models/domain_validation.py:270-307`
  `model_validate(...).model_dump(by_alias=True)` = **every field materialized with defaults** (A5).

**Registry** (`R/`, `S/shared_config/domain_config.py`): `dataSources` (61 paths; `DataSourceSpec` 305–316: domain, field,
kind, components, siblings, requiresEvidence, waitsForVisual, cap, outputWeight), `demoteTo` (+ note: "first component
the SAME dataSource lists"), `requirementEvidence` (tech: code_playground→has_code), `quizPolicy`, `quizEnrichment`,
`grouping {maxTextGroups 5, groupOutputBudget 1500}`. **No `fallbacks`/`upgrades` keys.** Waits-for-visual: the four
tech code paths + gaming.highlights. `tech.patterns`/`cheatSheet`/`setup.commands` have `requiresEvidence: null`.
`render_valid_datasources` 442 marks gated paths `[key]` in the toolkit (part of the plan's cached static half).

**Assembly / emission**: `P/phases/assembly.py:219-256` assembles once (`assemble_response`
`P/assembly/core.py:1411`, 1,930 lines, monolithic: resolve → sibling/cross-domain fallbacks 1095/1155 → assembler →
promote/density/demote → required backfill `_validate_domain_requirements` 693 (data-driven `_backfill_required_component`
528, evidence from `triage_dict["evidence"]` 1480) → frames (response-wide `used`, 235) → `_link_tabs`/`crossTabLinks`
1048 → `attach_secondaries` 1860–1877), streams every non-moment tab (`_first_tab_events` 178), late group 194–201
(moment fill ∥ synthesis ∥ quiz if `needs_quiz` `P/enrichment.py:107`), re-assembles with the quiz (`_add_late_quiz` 204),
`closing_events` (final re-emit) 248, `_finish` 320. TabLedger `P/tab_ledger.py`: `final` = nothing changes later
(`_settled` 103: false while quiz pending), a second final is a logged contract slip (139).

**Timing / tags**: `llm_feature_var` per phase (`summarize:extraction` `P/phases/extraction.py:237`); ledger rows
(`packages/llm-common/.../callback.py:248`) carry only `feature`; `pipeline.timing.llmCalls` carry feature + span
(`P/pipeline_timing.py:252`, `TIMING_SCHEMA_VERSION=2` 42); `scripts/cache_hit_rate.py` groups by span.
**Streaming: none** — `LLMProvider._run_completion` `S/services/llm_provider.py:198-249` awaits one `acompletion`.
**Replay**: fake `acompletion` keyed by feature + span + ordinal (`T/replay/fake_llm.py:80-117`, `cassette.py:40`);
a miss raises `CassetteMissError`; cassettes pin `EXTRACTION_PARALLEL_BATCHES: 2`; recorded plans are pre-1b (no
evidence, no briefs), memory synthetic (no evidence). Baseline of today's requests: `<scratch>/baseline/`
(`canonical_request` = sorted compact JSON; extraction sha256 T1dQ `fec7a4d9…`, jMq8 `bd18a3a7…`, uC45 `7d3f3fbe…`).

## 2. Measurements and the 1h-block decision

cl100k (tiktoken in the summarizer venv), converted to Anthropic tokens ×1.15 — calibrated on the plan static half
(14,173 cl100k ↔ 16,278 billed on Sonnet 4.6, g2); Haiku 4.5 assumed the same tokenizer family (3.4's live probe checks).

| Item | cl100k | Item | cl100k |
|---|---|---|---|
| extraction rules (system section incl. directive) | 1,002 | toolkit `component_toolkit.txt` | 2,950 |
| schemas: finance 368 · fitness 790 · food 1,012 · gaming 1,131 · language 737 · learning 871 · music 637 · narrative 480 | | news 989 · podcast 893 · project 741 · review 784 · science 771 · sport 1,247 · tech 614 · travel 924 | **Σ 12,989** |
| examples: fitness 1,360 · food 858 · learning 1,219 · music 1,073 | | project 1,241 · review 1,001 · tech 905 · travel 1,328 | **Σ 8,985** |

Per benchmark (today's prompt pieces; transcript = `render_transcript` of the cassette segments; memory ≈ 350):

| | domains (example) | block 1 requested (cl100k → Anth.) | block 2 transcript+memory | groups G (§3.3) | today's call (rec.) |
|---|---|---|---|---|---|
| T1dQhQAm8Tc | tech+learning (tech) | 1,002+614+871+905+40 = 3,432 → **3,950** | 5,603 → 6,445 | 3 text + visual | 10,643 in / 8,606 out / 76 s |
| jMq8lEu-of0 | food (food) | 1,002+1,012+858+40 = 2,912 → **3,350** | 3,793 → 4,360 | 3 text | 9,352 / 9,550 / 82 s |
| uC45_4nnEAI | gaming (none) | 1,002+1,131+40 = 2,173 → **2,500** | 5,806 → 6,680 | 2 text + visual | 10,408 / 4,674 / 50 s |
| ALL domains | 16 schemas + 8 examples | 1,002+12,989+8,985+~170 = 23,150 → **26,630** | | | |

**Haiku 4.5's minimum cacheable prefix is 4,096 tokens** (the whole prefix up to the breakpoint; below it the marker is
silently ignored). Dev and prod both run extraction on `anthropic/claude-haiku-4-5-20251001` (`.env`, context D-list).
Prefix input cost per run, Haiku $1/MTok, 5m write 1.25×, 1h write 2×, read 0.1× (token-equivalents → $):

| | REQ: requested domains (1h marker below the minimum → one 5m entry over block 1+2) | ALL, 1h entry warm | ALL, cold (first video of the hour) |
|---|---|---|---|
| formula | (1.25 + 0.1·(G−1))·(B1+B2) | 0.1·G·26,630 + (1.25 + 0.1·(G−1))·B2 | 2·26,630 + 0.1·(G−1)·26,630 + (1.25+0.1(G−1))·B2 |
| T1dQ (G=4) | 1.55 × 10,395 = 16.1k → **$0.016** | 10.7k + 10.0k → $0.021 | 53.3k + 8.0k + 10.0k → $0.071 |
| jMq8 (G=3) | 1.45 × 7,710 = 11.2k → **$0.011** | 8.0k + 6.3k → $0.014 | 53.3k + 5.3k + 6.3k → $0.065 |
| uC45 (G=3) | 1.45 × 9,180 = 13.3k → **$0.013** | 8.0k + 9.7k → $0.018 | 53.3k + 5.3k + 9.7k → $0.068 |

**Decision: block 1 = the run's requested domains' (+ active modifiers') FULL schemas + the primary example — not all
domains.** ALL loses even when warm (G calls each read 26.6k tokens: 0.1·G·26.6k ≥ 1.25·B1 for every G ≥ 2) and costs
≈ +$0.05 per cold video; prod traffic is sparse, so cold is the common case; 16 foreign schemas + 8 examples also widen
the "emit other domains" failure the tail must suppress. Considered: rules + all schemas without examples (16.1k): warm
T1dQ $0.018, cold $0.049 — still worse. Kfir's 1h marker stays on block 1 as specified (1h before 5m): it is a no-op on
all three benchmarks (< 4,096) and, when a 3-domain run crosses the minimum, it costs +0.75·B1 (≈ $0.004) repaid by
any same-domain-set run within the hour (golden, demo re-run). For reference today's T1dQ call pays ≈ $0.014 input; the
group path ≈ $0.019 input (prefix + tails incl. annotations) but drops ≈ 2,150 unrequested output tokens (−$0.011).

## 3. Interfaces

### 3.1 Field-aware renderer — `P/schema_fields.py` (new)
Parses the schema files the LLM already gets (via `load_prompt_text`), never edits them. All 16 parse as JSON once every
line starting with `>` (`> UI`, `> CONNECTIONS`, `> GROUPING`) is dropped (verified; `T/test_datasource_registry.py`
has a test-only `_schema_skeleton` that does the same — move it here and import it from the test).
```python
@dataclass(frozen=True)
class DomainSchema: name: str; skeleton: dict[str, Any]          # first JSON object, `>` lines stripped
def load_domain_schema(name: str) -> DomainSchema                   # lru_cache; unsafe/unknown name → empty skeleton
def scalar_fields(domain: str) -> tuple[str, ...]                   # top-level non-list keys that are not a registry
    # field or its parent: fitness meta · food meta · gaming title · language targetLanguage/nativeLanguage/level ·
    # learning keyQuestion/summary · music title/artist · news headline · podcast show/host · project projectName/
    # difficulty/estimatedTime/estimatedCost · review product/price/rating · science field/level · sport title ·
    # travel bestSeason · tech/finance/narrative none (tech.setup is the parent of tech.setup.commands)
def slice_skeleton(domain: str, fields: Sequence[str]) -> dict[str, Any]   # nested key tree, dotted paths kept
def render_emit_only(slices: Mapping[str, Mapping[str, Any]], caps: Mapping[str, int | None]) -> str
    # "Emit ONLY these keys (anything else is discarded): food.ingredients (cap 30), food.steps (cap 25), food.meta"
    # + "Output shape: {"food":{"ingredients":[…],"steps":[…],"meta":{…}}}" (leaves → […]/{…}/"…")
```
Consumers (nothing else is built): `scalar_fields` → 3.3 (injected, see 3.3); `render_emit_only` → 3.4 group tail;
`slice_skeleton` → 3.4 (shape line) and 3.5 (the key filter). **Not built** (deviation, Kfir's 3.4 directive makes
them dead): RULES/COMMON-MISTAKES slicing and example slicing — block 1 carries the full schema + example.

### 3.2 Reconcile at plan-done — `P/reconcile.py` (new) + the code-evidence rule
Evidence view (one rule for every reader downstream): `doubly_false_view(plan_ev, memory_ev) -> dict[str, bool]` —
True if either reader says true; **False only when BOTH answered false**; absent otherwise (memory None or silent → no
opinion → no demotion, Appendix C + `MemoryResult` contract). Replaces `plan_result.evidence` (raw plan answers kept in
`triage_dict["planEvidence"]`, memory's in `["memoryEvidence"]`), so `needs_quiz`, `video_memory`, assembly's backstop
(`core.py:1480`) and `demotion_blocked` all read the same view.
```python
@dataclass(frozen=True)
class ReconcileOutcome:
    tabs: list[dict]; dropped: list[dict]            # droppedTabs shape (id, component, dataSource, reason)
    records: list[dict]                              # {tabId, component, from, to, reason, action, stage, evidence}
    evidence: dict[str, bool]; deferred: tuple[str, ...]   # tab ids awaiting the screen-code check
def reconcile_at_plan_done(plan: PlanResult, memory: MemoryResult | None, content_format: str | None) -> ReconcileOutcome
def resolve_screen_code(tabs: list[dict], deferred: Sequence[str], frame_descriptions: Sequence[Mapping],
                        all_frames: Sequence[Mapping], active: Sequence[str], evidence: Mapping[str, bool]) -> ReconcileOutcome
def screen_shows_code(frame_descriptions, all_frames) -> bool   # any vision scene_type "code", or a scene_type
    # "screen_recording"/OCR line matching the command/code regex (`$ `, npm|npx|pip|git|docker|curl|brew …, `def `,
    # `function `, `const `, `import `, `=>`, `();`) — vision's vocabulary: S/prompts/vision.txt:2
```
Steps (each records `stage="plan_done"`), in order: (1) evidence: a tab whose dataSource `requiresEvidence` K is
doubly-false → re-point (existing `_replacement`, made public in `plan_reconcile.py`) → else **demote**: walk
`demoteTo[component]`, take the first rung that an *eligible* dataSource renders (siblings of the source, then its domain,
then active domains; eligible = active domain, its own evidence not doubly-false, `(source, component)` not taken, and
**not a source another tab already reads**) → moment_track kept for the fallback chain → else drop (`reconcile_evidence_false`).
**Code evidence (Kfir decision 7):** `R/` gains `requiresEvidence: "has_code"` on `tech.patterns`, `tech.cheatSheet`,
`tech.setup.commands` (tech.snippets already has it). Plan-time evidence = the toolkit's `[has_code]` marker
(rendered from the registry; plan.txt rule 535 "never plan a tab on a path whose evidence you marked false" — no prompt
edit). Enforcement: when `has_code` is doubly-false at plan-done the transcript has no code, but the screen may —
all four code paths are `waitsForVisual`, so their extraction cannot start before frames-done anyway: the tab is
**deferred** (`action="deferred_to_frames"`) and `resolve_screen_code` decides at frames-done: code on screen → kept
(`kept_screen_code`), else the step-(1) chain (re-point/demote/drop, `stage="frames_done"`). No vision/OCR (LOW tier,
frames off) → no screen evidence → decided on the transcript alone. Either reader true → kept. (2) Required backfill
(rule 3): every `effective_requirements(primary, fmt)["required"]` component missing (equivalents via
`COMPONENT_PROMOTIONS`) and not doubly-false per `requirement_evidence()` → append its defaultTab (`backfilled`) — the
group path only extracts what is requested, so assembly's data-driven backfill becomes a backstop. (3) Quiz: drop a
quiz tab when the primary is not in `quizDomains` or `is_learnable` is doubly-false (`quiz_dropped`); move it last.
(4) Dedupe `(dataSource, component)` (first wins). (5) 3–6: trim the tail beyond 6 (never a required tab), fill to 3
from primary defaultTabs (eligible, not forbidden). The plan-parse guard keeps registry validity + `inactive_domain`
only (its evidence trigger moves here — else the memory second opinion is dead).
Call sites: `P/phases/text.py` after plan ∥ memory: `outcome = reconcile_at_plan_done(...)` → store (plan_result copy,
triage, triage_dict, `reconcile` += records, `dropped_tabs` += dropped) → **`triage_complete` + `meta` move here** (from
`triage.py:119-120`; plan is usually the slower reader, so ≈ 0 s later) → `render_video_memory` on the reconciled plan.
Frames-done: false path → orchestrator `visual_inject` step (before `run_phase_extraction`, `pipeline_orchestration.py:233`);
true path → the frames-done hook (3.4) before the visual group fires. Both modes run it.

### 3.3 Grouping — `P/field_groups.py` (new, pure)
```python
@dataclass(frozen=True)
class FieldRequest:
    domain: str; field: str                  # field = key path in the domain object ("setup.commands", "meta")
    kind: Literal["list", "object", "scalar"]; reason: Literal["planned", "fallback", "upgrade", "scalar"]
    tab_ids: tuple[str, ...]; cap: int | None; expect: int; weight: int; visual: bool
@dataclass(frozen=True)
class ExtractionGroup:
    group_id: str                            # "text1".."text5" | "visual"; text1 = the warm-up call
    fields: tuple[FieldRequest, ...]; visual: bool; est_output_tokens: int; tab_ids: tuple[str, ...]
def requested_fields(tabs: Sequence[Mapping], content_tags: Sequence[str], modifiers: Sequence[str],
                     scalars_of: Callable[[str], Sequence[str]]) -> list[FieldRequest]
def build_groups(fields: Sequence[FieldRequest], tabs: Sequence[Mapping], config: GroupingConfig) -> list[ExtractionGroup]
def visual_group(fields: Sequence[FieldRequest]) -> ExtractionGroup | None   # re-cut after resolve_screen_code
```
Requested = ∪ over reconciled tabs (skipping overview, `frames`, `enrichment.*`) of: **planned** = the dataSource;
a bare domain / `<d>.*` source = every registry field of `d`; modifier fields (`finance.costs`) as given.
**fallbacks** = the registry `siblings` of each planned path whose domain is active (the registry documents them as
"can stand in when it is empty" = assembly's sibling swap). **upgrades** = side reads of the component that renders the
tab: flash_deck in learning/science → `<d>.concepts` (promotion to concept_canvas, `assembly/promotion.py`);
lyrics_karaoke → `music.structure` + `music.lyrics`; workout_room → `fitness.exercises/warmup/cooldown/timer`;
comparison on review → `review.pros/cons/comparisons` + scalar `product`. **scalars** = `scalars_of(domain)` for every
requested domain, placed in that domain's first group. Expect = `brief.expect` or, when 0/missing (fallback plans,
pre-1b cassettes), `ceil(cap/2)`; weight = `outputWeight` (scalars: 60 per domain). Partition by `waitsForVisual`;
text by domain (modifier = own bucket); a bucket whose Σ weight×expect > 1,500 splits greedily in plan order (a field
never splits; one over budget stands alone); groups ordered by their first served tab; > `maxTextGroups` → merge the
two smallest adjacent same-domain groups (then any) until ≤ 5; all visual fields = ONE group. **text1** = the text
group holding the first planned tab's field (a visual first tab → the next planned tab's text group). Called by 3.4's
member at plan-done; `scalars_of` is wired to `schema_fields.scalar_fields` there (keeps 3.1 ∥ 3.3 independent).
Cassette results: T1dQ text1 [tech.topics] · text2 [learning.timestamps + keyQuestion/summary] · text3 [learning.concepts]
· visual [tech.snippets, tech.patterns(fallback)]; jMq8 text1 [food.ingredients + meta] · text2 [food.steps] · text3
[food.tips]; uC45 text1 [gaming.rankings + title] · text2 [gaming.loadout] · visual [gaming.highlights].

### 3.4 Firing — `P/extraction_groups.py`, `P/extraction_group_prompt.py`, `P/phases/split_extraction.py`,
`P/extraction_chunks.py`, `P/phases/frames_done.py`, `S/services/llm_streaming.py` (new) + prompt `S/prompts/extraction_group_job.txt`
**Orchestration (true path):** `_run_phase_two` group = `[then_provisional_moments(run_phase_text),
then_frames_done(run_phase_frames), run_phase_split_extraction]`; `run_phase_text` sets `ctx.plan_done` (new
`asyncio.Event` on `PipelineContext`) after reconcile + video_memory; `then_frames_done` sets `ctx.frames_done` in a
`finally` (every early return of `run_phase_frames` 171–199 included) after rendering `ctx.visual_annotations` (same
function and inputs as `_render_visual_annotations`, step `visual_inject`). The member awaits `plan_done`, builds fields
→ groups (3.3), chunks (`plan_chunks(ctx)`: one chunk = `ctx.prompt_transcript` until 3.7), fires, yields per-group
`extraction_progress {section:"groups", batch:n, of:N}` and ONE `extraction_complete` (merged) at the end. After the group:
faithfulness + `run_phase_assembly` as today (3.6 adapts it). **False path:** `_run_phases_in_order` untouched except
3.2's frames-done reconcile call.
**Three blocks** (`build_group_messages(prompt: ExtractionPrompt, group, chunk) -> list[TextBlock]`, no system prompt):
reuse today's `build_extraction_prompt` (`extraction_prompt.py:316`, unmodified) and cut its pieces —
1. `text_block(prompt.system + <schema>…</schema> + <extraction_example…>…</extraction_example>, cache=True, ttl="1h")`
   (the two elements cut by tag from the bound tail; identical bytes for every group and chunk of the run);
2. `prompt.user_blocks(chunk.transcript)[0]` — today's head block, `cache_control` 5m (no ttl key), identical across a
   chunk's groups; 3. the group tail from `extraction_group_job.txt`: `<your_job>` = `{batch_context}` +
   `render_emit_only(...)` + "Tabs you serve:" (`render_tabs_to_serve` on this group's tabs) + `get_content_emphasis` +
   the empty-array line; visual group only: `<visual_context_guide>` + annotations (chunk slice) + `<key_frames>`.
   Order 1h → 5m → tail; 2 breakpoints (≤ 4). Register `summarizer:extraction_group_job` to DEV Langfuse.
**Calls:** per group task `llm_feature_var.set(f"summarize:extraction:{gid}")`, `stage_name=f"extraction_{gid}"`
(chunk k ≥ 1: `_c{k}`), `call_llm_with_retry(..., model_override=get_stage_model("extraction"), max_tokens=16384,
timeout=120, max_retries=1, json_mode=True)`. Ledger "cost per video by group" = `feature`; `cache_hit_rate.py` per span.
**Warm-first (Kfir round 3):** per chunk, text1 is the ONLY streamed call: `call_llm_with_retry(..., on_first_chunk=warm.set)`
→ `LLMService.call_llm` → `LLMProvider._run_completion` → `llm_streaming.stream_completion(kwargs, on_first_chunk)`
(`stream=True, stream_options={"include_usage": True}`, any first chunk — message_start = prefill done = entry readable —
releases; `litellm.stream_chunk_builder` rebuilds the response for `record_llm_call` + `ttftMs`; total timeout via
`asyncio.wait_for`). Other text groups: `await asyncio.wait_for(warm.wait(), GROUP_RELEASE_FALLBACK_S=1.5)` (timeout →
release anyway), then `async with semaphore`. text1's `finally` sets `warm` (failure before the first chunk releases).
Visual group: `await ctx.frames_done.wait()` → `resolve_screen_code` (3.2) → `visual_group(...)` re-cut (empty → not
fired) → `await warm.wait()` → semaphore. Semaphore = `EXTRACTION_PARALLEL_BATCHES` over all chunks × groups; text1
acquires first. Outcomes stream through an `asyncio.Queue` (pattern of `_chunked_extraction` 605–662).
`@dataclass class GroupOutcome: group: ExtractionGroup; chunk: int; data: dict | None; validation: ValidationReport;
status: Literal["ok","failed"]; wall_ms: int; output_tokens: int`. Interim validation/merge = today's
`validate_domain_output_with_drops` + `merge_batch_extractions` (3.5 replaces). `pipeline.timing` llmCalls gain
`ttftMs` (schema v3). Docs/comments: `EXTRACTION_PARALLEL` = "true: field groups (phase 3); false: today's single call
/ chunked batches one at a time" (config.py, INFRASTRUCTURE.md, SERVICE-SUMMARIZER.md — no env change).

### 3.5 Per-group validation, key filter, merge — `P/group_merge.py` (new)
```python
def validate_group(raw: dict, group: ExtractionGroup, content_tags, modifiers) -> tuple[dict, ValidationReport]
    # unwrap flat single-domain output; validate each domain block with today's per-item validator
    # (items keep their item-level defaults), then keep ONLY the group's key tree (slice_skeleton) → no
    # unrequested field is materialized; a requested field the model omitted stays absent
def merge_groups(outcomes: Sequence[GroupOutcome]) -> tuple[dict, ValidationReport]
    # chunk-major, then group order (text1..textN, visual); lists concat; scalars FIRST non-empty wins;
    # dedup identity keys in a FIXED order (name, label, term, title, ingredient, tool, location);
    # renumber only keys that held 1..n in every contributing list (step/order/index), never `number` otherwise
def requested_quality(fields, extraction) -> ExtractionQuality   # count/coverage on requested fields only
```
`extraction_data` stays `{domain: {...}}`; `merge_report` sums drops; `pipeline.extraction._groups = [{group, fields,
status, outputTokens, wallMs}]` (optional key in `extraction_record`, absent on the false path). `extraction_merger.py`
stays untouched (the false path's chunked merge keeps its semantics).

### 3.6 Per-group assembly + emission — `P/phases/group_emission.py` (new)
```python
def ready_tab_events(ctx, landed: frozenset[str]) -> list[str]
    # assemble_response over a deep copy of the merged-so-far extraction (no enrichment, memory-seeded synthesis);
    # emit each tab whose every FieldRequest landed (ok or failed), except overview, moment_track (held for the
    # fill, as today), quiz_arena; payload minus crossTabLinks + attachments; position = its index in that
    # assembly; ledger.event(..., final=False) — skipped when the same fingerprint went out before
```
Called by the member after each group lands. `TabLedger` gains `groups_pending` (`_settled` false while set);
`run_phase_assembly` in split mode skips `_first_tab_events` (every tab already went out once or is overview/moment/
quiz) and keeps the late group: moment fill ∥ synthesis ∥ the quiz **task** — the member starts `ctx.quiz_task` (new)
when every text group holding a field of a `quizDomains` domain has landed (the visual group only if no such text group
exists), input = the merged output so far + video_memory (B.6) — `run_phase_enrichment` awaits it instead of calling.
Then `_add_late_quiz` (attachments, quiz tab last, links) → `closing_events` = every tab `final:true` at its final
position, overview last-emitted → `complete`. Faithfulness after the member (frames-done guaranteed).

### 3.7 Long videos — `P/extraction_chunks.py`
`plan_chunks(ctx) -> list[ExtractionChunk(index, transcript, batch_context, start_s, end_s)]`: when
`needs_chapter_batching(duration, transcript)` is false → one chunk (no chapter work at all, no description wait);
else today's `_split_chapters` chain (creator chapters → description → memory outline; chapter_detect only without an
outline, D6) → `batch_chapters` (≤ 50k tokens / 40 min) → chunk transcript = `_build_batch_transcript`, context =
`_build_batch_context`, annotation window = `_batch_start_seconds` of chunk k / k+1 (reuse; make public in
`extractor.py` only if the false-path request stays byte-identical — else copy-free wrappers). Calls = chunks × groups
under the one semaphore; per-chunk warm events; merge chunk-major (3.5).

### 3.8 Failure paths — `P/group_failures.py`
A group = one `call_llm_with_retry(max_retries=1)` + parse; a parse failure → one more call (`max_retries=0`); still
nothing → `status="failed"`. Failed fields count as landed (tabs don't wait forever). Final assembly then drops/demotes
their tabs through today's fallbacks; `relabel_group_drops(dropped, failed_fields)` rewrites those droppedTabs reasons to
`extraction_group_failed`. All groups of every chunk failed → today's error (`FAILED`, `LLM_ERROR`, "Extraction failed
to produce data", `phases/extraction.py:295-308`). Plan failure → `_build_fallback_plan` tabs feed `requested_fields`
unchanged (expect defaults, no evidence → no reconcile action).

### 3.9 Version + prompts + demo re-run
`pipeline-version.json` v8 → v9 (bind-mounted single file → force-recreate summarizer, worker, api); DEV Langfuse:
`register_prompts.py --commit` (new `extraction_group_job`; plan static half changes through the registry markers →
first prod plan writes a new 1h entry ≈ $0.10); `scripts/demo_rerun.py` (new): reads a `demo-export.sh` `urls.txt` (or
v8 completed docs), dry-run default prints count × ≈ $0.15–0.33, `--apply` submits each via `_eval_api` with
`bypassCache` and consumes the SSE (it drives the pipeline), concurrency 1. Written, not run.

## 4. Byte-identity proof (`EXTRACTION_PARALLEL=false`)
Untouched on the false path: `extractor.extract` and below, `extraction_prompt.py` request bytes, `base_extraction.txt`,
`quality_rules.txt`, `schemas/*`, `examples/*` (no phase-3 prompt edits — the group tail is a NEW file),
`extraction_merger.py`, `phases/extraction.py` call path (only post-call helpers may be renamed public). New provider
code is additive: no `stream`/`stream_options` key unless `on_first_chunk` is passed. Shared inputs that could move the
request: plan tabs (reconcile), `video_memory` (evidence view, tab keys), registry (`requiresEvidence` feeds
`_relevant_keys`). On the 3 cassettes the recorded plans and the synthetic memory carry NO evidence → doubly-false
view `{}` → no evidence action; required components present (tech code_playground, food step_player+checklist, gaming
tier_list); T1dQ's quiz already last, primary tech ∈ quizDomains; no duplicate `(source, component)` after the
plan-parse re-point (jMq8 key_moments → food.steps moment_track); tab counts 5/4/3. ⇒ reconcile is a no-op on all
three, no neutralising needed. The test asserts that premise first (plan-done `pipeline.reconcile` records == [] and the
tab list equals the plan-parse list) so a future cassette that triggers reconcile fails loudly instead of confounding.
**Test** `T/replay/test_replay_extraction_identity.py` (3.4; committed fixture `T/replay/baselines/<vid>.extraction.json`
= the 41501dd capture's extraction line + sha256): replay each cassette with `EXTRACTION_PARALLEL=False` (harness
override), the fake records `canonical_request(kwargs)` per key, assert `summarize:extraction/extraction#0` sha256 ==
baseline and exactly one extraction call. Other requests are NOT asserted identical by design: the plan's static half
gains three `[has_code]` markers (3.2) and plan-done moves `meta`. Re-run at every phase-3 commit (part of the suite).

## 5. Replay with synthesized group timings
`T/replay/group_synth.py` (new, 3.4) + `fake_llm.py`: a miss on feature `summarize:extraction:*` is answered from the
cassette's recorded single extraction entry E (output, O tokens, L ms) — never a `CassetteMissError`:
- content = E's JSON sliced to the group's key tree (flat single-domain output re-wrapped under the domain; missing
  keys → `[]`/`{}`), so assembly sees the recorded data; the group's keys come from a harness ContextVar set by a
  patch around `extraction_groups.run_group_call(group, …)` (same trick as `_tag_span`, `fake_llm.py:120`);
- out tokens = O × cl100k(slice)/cl100k(all keys of E) (shares measured in §3.3 terms);
- wall = TTFT + out/R, R = O / (L/1000 − 1.0) (T1dQ 114.8, jMq8 117.3, uC45 95.8 tok/s), TTFT 1.0 s for text1 (cache
  write), 0.6 s for the rest; input usage: text1 cacheWrite = (block 1+2 chars)/3.6, others cacheRead the same;
- `stream=True` → async iterator: role chunk after TTFT·speed, content chunk after the rest, usage chunk.
Expected walls (speed 1, from plan-done): T1dQ text1 1.4 s · text2 14.3 s · text3 32.5 s · visual 10.9 s after
frames-done (Σ out 6,472 vs 8,606 recorded, −25 %); jMq8 22.8 / 41.5 / 12.9 s (Σ 8,800 vs 9,550); uC45 13.8 / 5.3 s ·
visual 7.0 s (Σ 2,291 vs 4,674, −51 %). `scripts/replay.py` prints per-group start/TTFT/wall/out and runs the group
replay at `EXTRACTION_PARALLEL_BATCHES=6` (prod value; cassettes pin 2). `test_should_use_every_recorded_llm_call` exempts
`summarize:extraction/extraction#0` in split mode (used by the identity test); `_TOP_LEVEL_PHASES` gains the split
names.

## 6. File ownership
Parallel trio (disjoint; a file needed by two has one owner, the other reports):

| Task | Owns (new = N) | Reads only |
|---|---|---|
| 3.1 | N `P/schema_fields.py`; N `T/test_schema_fields.py`; `T/test_datasource_registry.py` (import the moved skeleton helper only) | `S/prompts/schemas/*`, `R/`, `domain_config.py` |
| 3.2 | N `P/reconcile.py`; `P/plan_reconcile.py`; `P/plan.py`; `P/phases/text.py`; `P/phases/triage.py`; `S/routes/pipeline_orchestration.py` (frames-done call, false path); `R/` (3 × `requiresEvidence`, `demoteToNote`, `requirementEvidenceNote`); `S/shared_config/domain_config.py` (if an accessor is needed); `S/models/pipeline_types.py` (if `to_triage_dict` needs the raw evidence keys); N `T/test_reconcile.py`; `T/test_plan_reconcile.py`, `T/test_datasource_registry.py` (requiresEvidence rows — coordinate with 3.1: 3.2 owns this file, 3.1 reports its import change), `T/test_phase_plan_*.py`, replay order assertions | `video_memory.py`, `enrichment.py`, `assembly/promotion.py` |
| 3.3 | N `P/field_groups.py`; N `T/test_field_groups.py` | `domain_config.py`, `pipeline_types.TabBrief`, `R/` (no `requiresEvidence` dependency in its tests) |

So `T/test_datasource_registry.py` → owner 3.2; 3.1 reports the one-line import swap (or keeps its own copy of the
5-line helper until 3.2 lands — preferred: 3.1 does not touch the file). `R/` edits → `pnpm --filter @vie/shared build`
(dist untracked) + force-recreate summarizer + worker (single-file bind mount) before dev runs; api/web suites + `tsc`
run because `R/` is shared.
Sequential tasks (each may edit earlier owners' files):

| Task | Files |
|---|---|
| 3.4 | N `P/extraction_groups.py`, N `P/extraction_group_prompt.py`, N `P/extraction_chunks.py` (one chunk), N `P/phases/split_extraction.py`, N `P/phases/frames_done.py`, N `S/services/llm_streaming.py`, N `S/prompts/extraction_group_job.txt` (DEV registration), `S/services/llm_provider.py`, `S/services/llm.py`, `S/utils/llm_retry.py` (465 l. — kwarg only), `P/pipeline_timing.py`, `P/context.py`, `S/routes/pipeline_orchestration.py`, `P/phases/text.py`, `P/phases/extraction.py` (public post-call helpers), `S/config.py` comment + `docs/INFRASTRUCTURE.md` + `docs/SERVICE-SUMMARIZER.md` lines; tests N `test_extraction_group_prompt.py`, `test_extraction_groups.py`, `test_llm_streaming.py`, `T/replay/{group_synth.py,fake_llm.py,test_replay.py,driver.py,test_replay_extraction_identity.py,baselines/*}`, `scripts/replay.py` |
| 3.5 | N `P/group_merge.py`, `P/extraction_groups.py` / `P/phases/split_extraction.py` (swap interim), `P/phases/extraction.py` (`_groups`), N `T/test_group_merge.py` |
| 3.6 | N `P/phases/group_emission.py`, `P/tab_ledger.py`, `P/phases/assembly.py` (432 l. — hooks only), `P/phases/enrichment.py`, `P/phases/split_extraction.py`, `P/context.py` (`quiz_task`), N `T/test_group_emission.py`, `T/test_tab_ledger.py`, `T/replay/test_replay_tab_contract.py` |
| 3.7 ∥ 3.8 | 3.7: `P/extraction_chunks.py`, N `T/test_extraction_chunks.py` (synthetic 90-min transcript fixture) · 3.8: N `P/group_failures.py`, `P/extraction_groups.py` (failure hook), N `T/test_group_failures.py` — disjoint |
| 3.9 | `packages/shared/src/config/pipeline-version.json`, N `scripts/demo_rerun.py` (+ test), `docs/IDEMPOTENCY.md` version line |

No new setting, no env change (`env-changes.md` untouched); prod `.env` lacks `EXTRACTION_PARALLEL` → true → groups go
live on deploy; `EXTRACTION_PARALLEL=false` on the box is the rollback lever (state it in g3, do not apply). Never edit
`assembly/core.py` (1,930 l.) — everything per-group lives outside it.

## 7. Ambiguities, conflicts, risks (recommendation → coordinator decides)
1. **`tab_ready{final:true}` per group vs the ledger contract** (final = nothing changes later). Text groups land
   before frames-done (no thumbnails yet), attachments come "at the final re-emit", links "at the end", positions move
   when a later group fails → every early final would be followed by a corrected final (a logged contract slip).
   → Per-group emission `final:false` (content-complete; 2.2 renders it), final re-emit `final:true`. Gate 3's
   first-extracted-tab time counts it (`firstAssembledTabMs` already excludes only the provisional).
2. **demoteTo semantics.** `demoteToNote` demotes onto the SAME dataSource — whose evidence is doubly-false, so the
   demoted tab reads an empty field. → Demote onto an eligible source that renders the rung (§3.2), never onto a source
   another tab already shows (T1dQ's workflow step_player would otherwise become an info_grid copy of tech.topics);
   rewrite the note. Kfir's "demotion/drop" for code paths is met: no eligible rung → drop.
3. **Memory missing.** Appendix C: memory missing → no demotion; `merge_evidence` (`video_memory.py:127`) and the
   hotfix-2.1 guard act on the plan alone. → Strict doubly-false for reconcile + the downstream evidence view (spec +
   `MemoryResult` contract); memory answers every key on a normal run, so the difference is the intended second opinion
   plus the rare memory failure (the empty tab then drops at assembly). g3 question: align the rendered `✗` line.
4. **Moving the plan-parse evidence guard to plan-done** relaxes hotfix 2.1 when memory says true (more tabs kept,
   some empty → dropped at assembly). Required by "memory second opinion"; watch the golden (food-travel vlog
   `has_ingredients`, T1dQ). `meta` moves to plan-done so it never lists a tab reconcile already dropped.
5. **"code on screen in the visual annotations" vs reconcile at plan-done** → the deferral rule (§3.2): free in latency
   because all four code paths wait for visual anyway; vision `scene_type: code` or code-shaped OCR counts.
   Kfir's wording "requirementEvidence" → implemented as the per-path `requiresEvidence` (the key that gates a tab;
   `requirementEvidence.tech.code_playground` already says has_code). "code terms" not widened into the plan/memory
   definition (plan.txt:56, memory.txt:31 "actual code, commands or configuration") — widening would keep mention-only
   talks' code tabs, the failure Kfir wants caught.
6. **fallbacks/upgrades are not registry keys** → siblings (active domains) and the component side-reads table (§3.3).
   Cost: T1dQ requests tech.patterns (≈ 550 out tokens) for nothing if snippets land; report sibling-only fields'
   output tokens at g3 (FieldRequest.reason).
7. **Kfir's 1h block** is below Haiku's minimum on all three benchmarks (§2) — the cross-video reuse it implies needs
   the ALL-domains block, which costs more. Kept as specified; g3 question whether to keep the marker.
8. **Warm-first vs TTFT.** Cold TTFT for ≈ 10k uncached input may exceed 1.5 s → the fallback fires first and the others
   WRITE again (+1.15·(B1+B2) per extra writer ≈ $0.009–0.012 on the benchmarks). 3.4 records `ttftMs`; g3 question: tune the constant from data.
9. **Single heavy field is a long pole** (jMq8 food.steps ≈ 4.8k out ≈ 42 s; T1dQ learning.concepts ≈ 33 s): the budget
   splits domains by field, never a field. And **one visual group** for code tutorials (snippets+patterns+cheatSheet+
   setup ≈ 4–7k out after frames-done ≈ 45 s) can miss 60–65 s. As written; g3 questions (item-range split; ≤ 2 visual).
10. **First extracted tab ≤ 35 s** (g2's note for gate 3) holds only when some text group is small: first tab ≈
    plan-done (≈ 29 s) + 1 s + smallest group (T1dQ 1.4 s, uC45 5.3 s, jMq8 12.9 s). Report, don't reorder group 1.
11. **Text groups see no annotations/key frames** (B.5: visual group only) — food steps lose today's visual cues.
    As written; quality watch at golden.
12. **Requested-only output shrinks fallbacks**: assembly's cross-domain/equivalent-field/generic-longest-list fallbacks,
    overview `_collect_tips`, tip_callout attachments read unrequested fields → fewer rescues (output-changing, the
    intended "27 % unused"). Report per-video at g3.
13. **3.7 "chunks from the memory outline"** vs today's chain (creator chapters first) → keep the chain (all give
    boundaries; `batch_chapters` merges); outline wins when there are no listed chapters.
14. **Merge policy** (C12) only for groups; the false path's `merge_batch_extractions` keeps longest-string +
    hash-order quirks (untouched by design) — g3 question to align later.
15. **Commit-by-commit greenness**: 3.4 lands the group path end-to-end with interim validation/merge (today's
    functions) and today's monolithic assembly after the last group; 3.5/3.6 swap pieces in. 3.1/3.3 code is unused
    until 3.4 — acceptable (consumed by phase end); 3.2's frames-done function is live at once on the false path.
16. **Streamed usage**: LiteLLM must keep `cache_read_input_tokens` / `cache_creation` (1h/5m split) in the rebuilt
    response and fire the ledger callback once per stream — unverified. 3.4 DEV probe (≈ $0.01, state spend): same prompt
    streamed vs not; if fields drop, read the final chunk's `usage` directly.
17. **Partial assemblies**: `assemble_response` copies the extraction only shallowly (core.py:1433-1458) and item-level
    passes may touch nested dicts → deep-copy per partial call; a
    tab's per-group payload can differ from its final (frames, fallbacks) — expected, final re-emit wins.

g3 questions (outside the nine tasks): Q1 visual-group split; Q2 item-range split of one heavy list field; Q3 keep the
1h marker on a sub-minimum block; Q4 `GROUP_RELEASE_FALLBACK_S` after TTFT data; Q5 `merge_evidence` render line vs the
strict view; Q6 old merger's quirks on the false path; Q7 rollback lever `EXTRACTION_PARALLEL=false` (env only);
Q8 annotations for text groups (or `waitsForVisual` for food.steps on HIGH tier).

## 8. Acceptance tests (names; AC from the plan table + Kfir's sub-items)
- **3.1** `should parse every schema when directive lines are stripped` (×16) · `should slice only the requested keys
  and their parents when given registry fields` (×16, every registry field) · `should round-trip a slice through
  json.dumps/loads when nested paths are requested` (tech.setup.commands) · `should list the scalar fields when a domain
  has non-list keys` (16 rows incl. tech = none) · `should render the emit-only line with caps when a group has scalars`.
- **3.2** table-driven `should <action> when plan=<t|f|∅> memory=<t|f|∅|None>` over all 9 combinations ×
  {evidence-gated path, required component, quiz} · `should drop T1dQ's workflow tab when has_code is false in both
  readers and no frame shows code` · `should keep the workflow tab when a frame's scene_type is code` · `... when OCR
  shows a shell command` · `should defer code paths at plan-done and decide at frames-done` · `should decide on the
  transcript alone when vision is off` · `should demote onto an eligible source when the rung exists` · `should drop
  rather than duplicate a source another tab shows` · `should backfill each domain's required component unless doubly
  false` (14 domains) · `should drop the quiz when the primary is not a quiz domain` · `should move the quiz last` ·
  `should dedupe (dataSource, component)` · `should fill to 3 / trim to 6` · `should emit meta after reconcile` ·
  `should record stage and evidence in pipeline.reconcile` · `should mark tech.patterns, cheatSheet and setup.commands
  [has_code] in the toolkit`.
- **3.3** `should request planned + active siblings + side reads + scalars when tabs are reconciled` · `should put
  waitsForVisual fields in one visual group` · `should split a domain by field when weight×expect exceeds the budget` ·
  `should never split a single field` · `should cap text groups at maxTextGroups by merging the smallest` · `should
  make the first planned tab's text group text1` · `should skip to the next text group when the first tab is visual` ·
  `should default expect to ceil(cap/2) when the brief has none` · the three cassette compositions (§3.3).
- **3.4** `should stream only text1 and start the others after its first chunk` · `should release the others after
  1.5 s when no chunk arrives` · `should release when text1 fails before its first chunk` · `should fire the visual
  group only after frames_done` · `should never exceed EXTRACTION_PARALLEL_BATCHES calls in flight` · cache layout:
  `should send block 1 with ttl 1h before block 2 with 5m` · `should hash block 1 and block 2 identically across
  every group of a run` · `should carry no system prompt` · `should tag each group call with its own feature` ·
  `should record ttftMs for the streamed call` · `should send today's exact extraction request when
  EXTRACTION_PARALLEL is false` (×3, baseline sha256) · `should set frames_done when frames return early`.
- **3.5** `should keep only the group's own keys when the model emits extra domains` · `should not materialize
  unrequested fields` · `should keep item-level defaults` · `should merge chunk-major then group order` · `should
  keep the first non-empty scalar` · `should dedupe by a fixed identity-key order regardless of hash seed` · `should
  not renumber shirt numbers` · `should count coverage on requested fields only` · `should leave extraction_data
  domain-keyed`.
- **3.6** `should emit a tab only when all its fields landed` · `should emit per-group tabs non-final and every tab
  final once at the end` · `should never emit overview, moment_track or quiz per group` · `should strip links and
  attachments from per-group payloads` · `should start the quiz when its owning text groups landed` · `should run
  synthesis, moment fill and faithfulness after the last group` · `should emit no duplicate payload for a tab` (replay
  tab contract ×3).
- **3.7** `should make one chunk when the video is under 40 min and 50k tokens` · `should run chunks × groups under
  one semaphore for a synthetic 90-min transcript` · `should slice annotations per chunk for the visual group` ·
  `should warm each chunk's prefix with its own text1` · `should call chapter_detect only without an outline`.
- **3.8** `should ship the other groups' tabs and record extraction_group_failed when one group fails twice` ·
  `should retry once on unparseable JSON` · `should fail the run as today when every group fails` · `should request the
  fallback plan's default fields when the plan call fails`.
- **3.9** `should report v9 on new docs` (config reads the file) · `should list videos and cost without submitting when
  demo_rerun runs dry` · `should register extraction_group_job` (register_prompts sync test).
- **Verification** (coordinator): replay ×3 with synthesized group timings (no miss, identity test green, walls per
  group) · full golden once, alone, concurrency 3 · prod benchmark 3 × 2 + regression after merge · cost per video by
  group from `llm_usage.feature` · cache hit rate per call type (`extraction_text1`… spans).

## 9. Coordinator decisions on §7 (2026-10-12, binding for every phase-3 agent)
- **A1** per-group `tab_ready` = `final:false` (content-complete, non-provisional); the closing re-emit sends every tab
  `final:true` once. Deviation from the brief's literal "tab_ready{final:true}" — the brief itself adds attachments and
  links at the final re-emit, and the 2.1 ledger contract says final = nothing changes later. Reported in g3.
- **A2** demoteTo → an eligible source that renders the rung, never a source another tab already shows; no eligible
  rung → drop. `demoteToNote` rewritten (3.2).
- **A3** strict doubly-false (both readers answered false) for reconcile AND the downstream evidence view; memory
  missing/silent = no opinion. The rendered `✗` line stays on `merge_evidence` → g3 Q5.
- **A4** the plan-parse evidence trigger moves to plan-done reconcile (registry validity + `inactive_domain` stay at
  parse); `triage_complete` + `meta` are emitted after reconcile. Dev runs report the meta/hero time change.
- **A5** code evidence = `requiresEvidence: "has_code"` on tech.patterns / tech.cheatSheet / tech.setup.commands;
  plan-time = the toolkit's rendered `[has_code]` marker (no plan.txt edit); doubly-false at plan-done → deferred to
  frames-done → `screen_shows_code` keeps, else re-point/demote/drop; no vision → transcript alone. "code terms" is NOT
  widened into the plan/memory evidence definitions (they already say "actual code, commands or configuration") —
  stated as a reading of Kfir's decision 7 in g3.
- **A6** fallbacks = registry siblings in active domains; upgrades = the component side-read table (§3.3).
- **A7** the 1h marker stays on block 1 as Kfir specified (no-op below 4,096 tokens) → g3 Q3.
- **A8–A12** as written; measured/reported at g3 (Q1, Q2, Q4, Q8 + the first-extracted-tab line).
- **A13** 3.7 keeps today's chapter chain (creator → description → outline; chapter_detect only without outline).
- **A14** C12 merge policy on the group path only; `extraction_merger.py` untouched → g3 Q6.
- **A15** 3.4 lands the group path end-to-end with today's validation/merge/assembly; 3.5 and 3.6 swap pieces in.
- **A16** 3.4 may run ONE DEV streaming-usage probe (≈ $0.01, Haiku 4.5, same prompt streamed vs not) and reports it.
- **A17** deep-copy the extraction per partial assembly.
- `test_datasource_registry.py`: owner 3.2. 3.1 does not touch it; 3.2 swaps `_schema_skeleton` to the
  `schema_fields` import once 3.1 is committed (the coordinator tells 3.2).
