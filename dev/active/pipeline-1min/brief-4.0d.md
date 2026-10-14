# Brief 4.0d — offline A/B per call type → model picks (after 4.0b is committed)

Kfir (tasks.md 4.0 d): "Offline A/B on the golden transcripts and cassettes (~$1, like 0.8): probe
(tier agreement), memory, extraction groups (validation drops, `_defaulted`, latency, cost), plan
(tabs chosen, evidence, output tokens, latency), vision as above, the OpenAI calls (quiz, Hebrew →
English translation with the echo check, description analysis). Pick per call type; defaults in
config; dev `.env`; prod values into `env-changes.md` for the one prod edit at the end." Pick rule
(4.0 header): quality within noise or better FIRST, then latency, then cost. Targets per call type =
the 4.0 list (plan → `claude-sonnet-5-5`; vision → `claude-haiku-5-5` vs `claude-sonnet-5-5`,
Sonnet 5.5 if Haiku misreads screen text; extraction groups, memory, tier probe → `claude-haiku-5-5`;
quiz, output + RAG translation, description analysis → `gpt-6-luna`; faithfulness judge stays
gpt-4o-mini until the comparison is done, then `gpt-6-luna`; Haiku 4.5 + Sonnet 4.6 stay as
fallbacks). Synthesis and chapter_detect are NOT in the list → keep today's model, g4 question.
Kfir: "Measurement runs — the A/B … run alone, nothing else executing … State the spend before the
A/B." → you BUILD the tool while other agents work, then STOP before the live run; the coordinator
states the spend and clears the box; then you run it.

Read: `agent-rules.md` (phase-4: ownership — you own LLM plumbing, `src/config.py`, compose model
lines, `env-changes.md`, the A/B scripts), tasks.md 4.0 (incl. "Checked against the code" facts: the
routing of every call type and what moving each setting moves), context.md D29 + "4.0c docs facts",
the 4.0b commit (its guard API — call through it so every arm gets its model's params),
`scripts/tier_probe_ab.py` + `_tier_probe_inputs.py` (0.8's A/B — reuse), `scripts/spotcheck_frame_vision.py`
(D3 rebuild base), `gates/g0-tier-probe-ab.md` (0.8's report shape), `gates/g2.md` (Hebrew/RAG
translation numbers, echo check), backend-python skill + ai-integration.md.

## The A/B tool (committed; files < 500 lines, split helpers)
- **Requests:** the real pipeline requests, not re-written prompts. Source 1 = the 3 replay
  cassettes (T1dQhQAm8Tc, jMq8lEu-of0, uC45_4nnEAI) in split mode via `<scratch>/tools/capture.py`'s
  mechanism (every request kwargs per feature/span): tier_probe, description, plan, memory, every
  extraction group, quiz (enrichment), faithfulness, vision. Source 2 = golden transcripts for the
  tier probe (18 live videos, `tier_probe_ab.py` inputs + labels). Source 3 = EqaMrzd9nZU (Hebrew) for
  output + RAG translation (inputs from dev Mongo/S3 read-only, the pipeline's own builders).
- **Arms:** A = today's model, B = the 4.0 target (vision: C = `claude-sonnet-5-5` too); B at the
  4.0b defaults (thinking off, effort low). Plan: if B shows a quality loss at low, one extra arm at
  medium (Kfir: "plan at medium only if the A/B shows a quality loss"). Same request bytes per arm —
  only the model (and the guard params it implies) change. Each request once per arm; plan +
  extraction twice on one video to show run-to-run noise.
- **Scores (the pipeline's own validators/parsers):** tier probe → agreement with the golden labels
  (domain / format / has_visual_demo / transcript_usable), parse failures. Plan → `PlanResult` parse,
  tabs chosen (Jaccard vs A, required tabs present), evidence agreement, output tokens, latency.
  Memory → parse, outline/chapters, evidence flags vs A. Extraction groups → per-group validation
  (group_merge: dropped items, `_defaulted`, requested keys filled, item counts vs A), output tokens,
  TTFT (streamed text1), latency, `finish_reason == length`. Vision → D3 measures rebuilt on
  `spotcheck_frame_vision.py`: selection overlap vs today, uC45 card-text reading (5 cards, 720p),
  scene_type agreement, latency, cost. Quiz → validation, question count. Description analysis →
  parse + field agreement. Translation → the echo check (output still in the source script),
  length ratio, RAG blocks left untranslated. Faithfulness judge → score agreement gpt-4o-mini vs
  gpt-6-luna on the same items.
- **Per call: cost (ledger-equivalent `completion_cost` — Haiku 5.5 priced by 4.0b's map entry),
  latency, input/output tokens, cache reads/writes, refusals.** Report max prompt tokens per call type
  per model (new tokenizer ≈ +30 %) and a projection for the longest chunk (3.7: ≤ 50k cl100k tokens)
  vs Haiku 5.5's 100K price tier.
- `--dry-run` (render + count, no LLM calls) and `--rescore` (re-score saved answers) like 0.8.
  Writes `gates/g4-model-ab.json` + a markdown table `gates/g4-model-ab.md` (shape like
  `g0-tier-probe-ab.md`). No Langfuse, no Mongo writes, no pipeline run.

## Then (after the run and the coordinator's OK on the picks)
- Defaults in `config.py` per call type (+ config test); compose anchors pass them through
  (literal defaults; 8 touch points — docs/env-example lines are 4.1/4.2's: list them in your report);
  `env-changes.md`: the prod `.env` lines to change (prod pins `LLM_EXTRACTION_MODEL`,
  `LLM_ENRICHMENT_MODEL`, `LLM_VISION_MODEL` today; read the "Checked against the code" routing) —
  never touch a real `.env` (the coordinator edits the dev `.env`).
- Faithfulness: rides the fast tier today; moving it alone needs a per-stage setting (moving
  `LLM_FAST_MODEL` would also move synthesis + chapter_detect, which are not in the list) → propose
  `LLM_FAITHFULNESS_MODEL` only if the comparison favours gpt-6-luna; say so.
- The `EXTRACTION_PARALLEL=false` identity test keys on prompt bytes (4.0b) → model defaults don't
  break it; re-run it.

Spend ceiling for the live A/B: **$1.50** (the coordinator states the estimate to Kfir first) — the
tool prints a dry-run cost estimate per arm before any call and aborts above the ceiling.
Flow: build + tests + dry run → STOP, report the dry-run estimate → (coordinator: spend stated, box
clear) → live run → STOP, report the tables + proposed picks → review R4d → fold → commit
`p4.0d feat(summarizer): model refresh A/B + per-call-type defaults` (tool + config + compose +
env-changes.md + tests).

## Also in the 4.0d commit (routed from 4.1 — config/compose are 4.0's)
Dead settings, no reader: delete `LLM_MAX_TOKENS`, `LLM_FAST_MAX_TOKENS`, `SCENE_MAX_FRAMES` from `src/config.py`;
`LLM_MAX_TOKENS` / `LLM_FAST_MAX_TOKENS` lines from both compose anchors (`docker-compose.yml` ~21–22,
`docker-compose.prod.yml` ~41–42); add the three to `tests/test_config.py` `_DELETED`;
`tests/test_gemini_transcriber.py:660-664` reads `settings.LLM_MAX_TOKENS` → compare against a literal or delete that
test; fix the `gemini_transcriber.py:50` comment. Re-verify each has no reader first. (`gates/g4-notes.md`.)

## Also (from 4.0b's report)
- `MODEL_CHAR_LIMITS` (`utils/llm_retry.py`) has no entry for the 5.5 models / gpt-6-luna (300K-char default) — decide
  with the A/B's max prompt tokens (the 100K-token Haiku 5.5 price tier is not enforced anywhere); report.
- llm-common `_CACHE_RATES_USD_PER_M` lacks the 5.5 / gpt-6-luna rates → `cache_savings_usd` 0 + a
  `cache.rate_missing` log per model: add the chosen models' rates (docs prices, context.md 4.0c) with a test.
