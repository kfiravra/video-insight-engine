# LLM Cost Model: output-quality calls

The ledger-visible calls the output-quality task (2026-10) added or changed. Companion to [llm-cost-model.md](./llm-cost-model.md) (schema, cache layout, pricing, audit); the rates and cache blocks cited below are defined there.
## Output-quality phase 2: the calls that changed

Three ledger-visible changes since `PIPELINE_VERSION` v10 shipped; each has a flag
(`HERO_REWRITE_ENABLED`, `QUIZ_V2_ENABLED`, both default `true`; [INFRASTRUCTURE.md](./INFRASTRUCTURE.md)) that
restores the previous call. The `feature` column is `llm_usage.feature`
(`llm_feature_var`); the span is the Langfuse generation and `pipeline.timing`
`llmCalls[].span` name ([OBSERVABILITY.md](./OBSERVABILITY.md#trace-structure)).

| Call | Span / stage | Model | `feature` | When it bills |
|------|--------------|-------|-----------|---------------|
| Hero rewrite | `hero_rewrite` | `LLM_EXTRACTION_MODEL` (Sonnet 5.5) | `summarize:hero_rewrite` | `HERO_REWRITE_ENABLED`: once per run (a retry is a second row), fired at text-groups-done; reads the groups' cached blocks (above). **Replaces** the synthesis call |
| Synthesis | `synthesis` | the fast tier | `summarize:synthesis` | the flag off, or the hero call gave no hero (`phases/synthesis._synthesis_dict`) — then exactly as before |
| Quiz v2 | `quiz_v2` | `LLM_EXTRACTION_MODEL` (Sonnet 5.5 since 4.6e, reading the groups' cache; no model key of its own) | `summarize:enrichment` | `QUIZ_V2_ENABLED`, an instructional format (`quizPolicy.formats`, 4.6e), a plan with a quiz host: one call per part holding a target key idea — each extraction chunk of a chunked run (S01 / S03 / S14 / S15 2 rows, S02 3), else each third (up to 3 rows) — in parallel; one call when the length or the targets' stamps are missing, or a one-chunk quiz has 3 questions — ≤ 15 min, 30 s per attempt, 1 retry, every call inside one 40 s stage deadline; a call whose reply parses no question, even item by item, is made once more (a row of its own; output-quality 3.0, G2-1 / G2-4). **Replaces** `enrich_quiz` |
| Quiz v1 | `enrichment` | `LLM_ENRICHMENT_MODEL` (gpt-4o-mini) | `summarize:enrichment` | `QUIZ_V2_ENABLED=false` |
| Judge: hero + quiz | `faithfulness` (up to two calls) | `LLM_JUDGE_MODEL` (gpt-6-luna) | `summarize:faithfulness` | after assembly, when the doc has a stamped takeaway / quiz question, the sample rate is above 0, and — per call — the hero rewrite wrote the hero / `QUIZ_V2_ENABLED` is on (flag-off runs make no extra judge call) |

- **Quiz v2 reads the extraction's cache (4.6e).** Until 4.6e it read none (Haiku 5.5, the
  transcript inline: S01 ≈ 21 K in + ≈ 2 K out ≈ $0.003). Each call now reads block 1 + its
  chunk's head at 0.1x ([cache layout](./llm-cost-model.md#where-cache_control-is-set)); the inline path (no group
  blocks) pays Sonnet's full input rate for its transcript.
- **The hero's bill is the job block plus the extraction's reads.** Blocks 1 (+ 2) are
  priced as cache reads when the group calls wrote them (the rates in the table in
  [llm-cost-model.md](./llm-cost-model.md#how-1h-writes-are-priced)), block 3 is plain input, the answer is capped at 2 000 tokens. The synthesis
  call it replaces (≤ 1 200 tokens out, fast tier) is no longer made on a run that gets a hero.
- **The judge's two extra calls** are bounded by prompt caps: at most ≈ $0.0021 per run
  (≈ $0.0013 at English's ~4 characters a token — `faithfulness_hero_quiz` docstring);
  ≈ $0.0004 per video was measured over the 13 gate-1 docs
  (the output-quality task, 2026-10, step 2.4). The extraction sample's
  ≈ $0.002–0.003 per video is unchanged.
- No `PIPELINE_VERSION` bump: the stored shapes the new calls write are additive
  ([DATA-MODELS.md](./DATA-MODELS.md#output-quality-hero-and-quiz-2026-10)).

## Output-quality phase 3: merge_rank

One added call, on chunked runs only (> 40 min; `MERGE_RANK_ENABLED`, default `true` —
`false` keeps the code merge alone):

| Call | Span / stage | Model | `feature` | When it bills |
|------|--------------|-------|-----------|---------------|
| merge_rank | `merge_rank` | `LLM_MEMORY_MODEL` (Haiku 5.5; no model key of its own — a blank setting follows memory's) | `summarize:merge_rank` | a split run of more than one chunk: once, at text-groups-done beside the hero (a wrapper retry or the one re-ask after a reply that is no JSON object is a second row); 12 s per attempt, the stage inside 25 s |

- **No cache.** The input is code-rendered lists, not a prefix any other call sends.
  Offline renders of the stored extractions: S02 (83 min, 113 items) ≈ 27.8 K characters
  ≈ 9.3 K tokens with the rules, S13 (42 min, 52 items) ≈ 13.6 K ≈ 4.5 K — billed 5 408
  in / 324 out on the p3i3 run (ids and clocks tokenize denser than prose); the answer is
  ids only. ≈ $0.001–0.002 a long video on Haiku 5.5 ($0.10 / $0.50 per
  MTok); ≤ 40-min runs never make the call.

## Output-quality phase 4: the English beneath verbatim quotes

| Call | Span / stage | Model | `feature` | When it bills |
|------|--------------|-------|-----------|---------------|
| Verbatim lines → English | `translation_full` (source → en) | `LLM_TRANSLATION_MODEL` (gpt-6-luna) | `summarize:translation` | a non-English run with a verbatim quote or verified question: one call per ≤ 2,500-char / ≤ 100-string batch of distinct lines, in parallel with the output translation; English runs never |

S15: 25 lines, ≈ 1.6 K chars → 1 call, ≈ 1 K in / 0.5 K out ≈ $0.0004 ($0.10 / $0.50 per MTok); the non-English extraction block 1 is 69 bytes shorter.
