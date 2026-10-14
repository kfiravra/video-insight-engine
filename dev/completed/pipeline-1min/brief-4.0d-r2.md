# Brief 4.0d round 2 — model A/B round 2 (Kfir 2026-10-13 evening, context.md D30)

Kfir's order (verbatim):
> A/B round 2 — ceiling $1.00, append to gates/g4-model-ab.{md,json}; nothing else runs meanwhile.
> 1. Extraction, four arms on the three cassettes ×2 — Haiku 4.5 as today, Haiku 5.5 low, Haiku 5.5 medium, Sonnet 5.5
>    low — with one completeness rule added to the prompt: every distinct step, item or command the transcript supports is
>    emitted, never merged or skipped; still no counts or floors. Item count is not the quality measure, since Haiku 4.5
>    pads to the caps: a reviewer agent diffs each arm's items against Haiku 4.5's using the transcript and classifies every
>    item one arm has and the other lacks as transcript-supported or padding. An arm passes if in both runs it lacks no
>    transcript-supported item (jMq8's preheat and covered bake included), filled_share ≥ A, defaulted ≤ A. Report per arm:
>    that diff, padding count, p50/p95, cost per call. Keep the rule only if Haiku 4.5's own output stays within its noise.
> 2. Plan at effort high, thinking off, four inputs ×2: p50, duplicate_sources, evidence agreement, required tabs vs medium;
>    and whether the plan's duplicate sources are dropped downstream or cost a group call.
> 3. Memory: Haiku 4.5 ×2 on the three cassettes for its run-to-run evidence agreement.
> 4. Per-video cost table on the four benchmarks: today's v9 vs stack 1 (Sonnet 5.5 plan/vision, Haiku 5.5 extraction +
>    memory) vs stack 2 (Sonnet 5.5 plan/vision/extraction, Haiku 5.5 memory).
> Decisions now, record them: tier probe stays Haiku 4.5, final; PIPELINE_VERSION bumps to v10 in the picks commit, not
> before. Stop after the report; no picks applied, no golden.

## Read first
`agent-rules.md` (phase-4 section + git + tests), `brief-4.0d.md` (round 1 design), the tool (`scripts/model_refresh_ab.py`
+ `scripts/_model_ab_*.py` + `services/summarizer/tests/test_model_refresh_ab_*.py`, committed in bac879b),
`gates/g4-model-ab.md` (round 1), `gates/g4-review-findings.md` §R4d, context.md D29/D30 + "4.0c docs facts".

## Ground rules
- Cache dir (inputs, answers.jsonl, dry run, review dumps, PROGRESS.md for restarts):
  `dev/completed/pipeline-1min/tools/out/ab-r2/` — persistent, gitignored (the round-1 dir died with /tmp).
- Spend: ceiling **$1.00 for round 2, cumulative** across invocations (answers.jsonl in the new dir). The tool aborts above it.
- **No live call until the coordinator's GO.** Build + dry run → STOP → report. After GO: live run → STOP → report.
  Nothing else may run during the live run (no suites, no rebuilds).
- No prompt-file edits, no config/compose edits, no picks, no PIPELINE_VERSION change, no Langfuse writes.
- Files < 500 lines (split helpers), functions < 50 lines, tests for new logic, ruff + pyright clean. Heavy lock:
  `flock /tmp/vie-heavy.lock -c '<cmd>'`. Don't commit (coordinator reviews first, then you commit `p4.0d` round 2).

## What to build
1. **Capture, model-pinned.** Since bac879b the config defaults are the 4.0d picks (plan Sonnet 5.5 + PLAN_EFFORT medium,
   vision Sonnet 5.5, description/translation gpt-6-luna, quiz gpt-4o-mini). Re-capture the 3 cassettes into the new cache
   dir with the round-1 TODAY models pinned (patch settings / MODEL_MAP / stage overrides during capture) so the captured
   kwargs stay model-agnostic and carry no thinking/effort. Identity check: round-1 rows carry no request hash → recompute
   `prompt_tokens_cl100k` per request and require it equal to round 1's stored value (json `requests`) for every request
   round 2 sends; any mismatch → STOP and report.
2. **Completeness rule (extraction only, all groups, all four arms, identical bytes):**
   `Completeness: emit every distinct step, item or command the transcript supports — never merge two into one, never skip one.`
   Insert it in the static (cached) part of the extraction request: into the system message's rules section if it has
   one, else as the system message's last paragraph. Nothing else changes (no counts, floors, anti-padding text). A
   request-variant on the arm (e.g. `Arm.variant="completeness"`), applied before `request_for_model`; record the
   insertion point + bytes in the report.
3. **Arms.**
   - extraction (cassette groups: T1dQ text1/text2/text3/visual, jMq8 text1/text2/text3, uC45 text1/text2/visual):
     `A+rule` Haiku 4.5 (round-1 A params), `B+rule` Haiku 5.5 low, `B-medium+rule` Haiku 5.5 medium, `C+rule` Sonnet 5.5 low
     (thinking `between_tools`) — repeats 2 on all three cassettes. Plus `A` (no rule) one extra run on jMq8 + uC45, so
     Haiku 4.5's no-rule noise floor exists on all three cassettes (round 1 has T1dQ ×2, jMq8 ×1, uC45 ×1).
   - plan: `B-high` Sonnet 5.5 effort high, thinking off (`between_tools`; effort ≤ high is allowed) — repeats 2 on FOUR
     inputs = the 3 cassettes + EqaMrzd9nZU. Eqa has no cassette: build its plan request with the pipeline's own plan
     builders (`render_plan_parts` etc.) from dev data (Mongo/S3 read-only; the latest completed v9 dev run of
     EqaMrzd9nZU), pinned to round-1 TODAY. If a faithful Eqa input isn't possible, STOP and say why — don't fake one.
     Medium baseline for the comparison: round 1 has `B-medium` on T1dQ ×2, jMq8 ×1, uC45 ×1. OPTIONAL (only if the
     total fits): `B-medium` ×2 on Eqa + one extra medium run on jMq8 + uC45 (medium ×2 on all four).
   - memory: `A` Haiku 4.5 — repeats 2 on the three cassettes (fresh; round 1's single A run is a third sample).
4. **Order for the cache** as round 1 (a model's repeats of one cassette back-to-back inside the 5m window; plan calls of
   one arm consecutive after one 1h write). Concurrency as round 1.
5. **Dry run prints** the wire check for every new arm (no PROBLEM lines allowed) and the estimate split into REQUIRED
   (everything above except OPTIONAL) and OPTIONAL, per call type and arm, against the $1.00 ceiling.

## Scoring + report (after the live run)
- **Fix the median**: `_model_ab_report.py` "p50" takes the lower-middle value for even n → true median (mean of the two
  middle values) + p95; re-render round 1 through `--rescore` (offline) keeping its Picks section; round-2 tables use it.
- **Reviewer dump** (coordinator runs the reviewer agents): per cassette `ab-r2/review/<vid>.json` + readable `.md` —
  for every requested LIST field of every group: each output's items with stable ids (`<arm>.<run>.<field>.<n>`) for all
  round-2 extraction outputs + round-1 A runs (no rule) + the extra A runs; plus the transcript text that group saw (from
  the request messages). The reviewer returns `ab-r2/review/<vid>.classified.json`:
  `{"field": [{"cluster": "<id>", "members": ["<item id>", …], "verdict": "supported"|"padding"|"unclear",
  "evidence": "<transcript quote> @ <mm:ss>"}]}` (members = items that are the same step/item across outputs).
- **Extraction verdicts** from the classification: per arm per run vs `A+rule` (union of its two runs): supported items
  the arm lacks (list them), supported items the arm has that A lacks, padding count (per output); filled_share,
  defaulted vs A; PASS = in both runs lacks no supported A item (jMq8 preheat + 375 °F covered bake checked by name),
  filled_share ≥ A, defaulted ≤ A. Also vs the union of all outputs (supplementary). Latency median / p95, TTFT (text1
  streams), cost per call, tokens in/out.
- **Keep-the-rule check:** per cassette, cluster-set distance (Jaccard over supported + padding clusters, and supported
  items lost/gained) between `A+rule` and `A` (no rule) vs the run-to-run distance of `A` (no rule) and of `A+rule`;
  within noise = the rule's distance ≤ the larger run-to-run distance on every cassette. State yes/no with the numbers.
- **Plan:** per arm (A round 1, B-medium, B-high): median / p95, distinct + duplicate sources, required_present,
  evidence agreement vs A and vs medium, evidence flips, tabs Jaccard, output tokens, cost per call. **Downstream:** run
  every plan answer (A, medium, high) offline through the real code that turns a plan into tabs + extraction groups
  (`plan_reconcile.reconcile_plan_tabs`, the split extraction's group builder) and report per answer whether a duplicate
  dataSource is dropped, kept as a second tab, or adds a group call (group calls vs the deduped plan); add the code path
  with file:line.
- **Memory:** `A` run-to-run evidence agreement + outline delta (3 cassettes × the 2 new runs, + round 1's run), next to
  round 1's Haiku 5.5 agreement 0.82.
- **Cost table (offline, `_model_ab_stack_cost.py`):** per benchmark video — T1dQhQAm8Tc r2, jMq8lEu-of0 r2,
  uC45_4nnEAI r2 (with frames), EqaMrzd9nZU r1 — from `gates/g3-prod-benchmark.json` `runs[].doc.llm` (per call: span,
  model, in, out, cr, cw1h, cw5m, usd): (a) today's v9 (re-priced from the records with LiteLLM's map — must match
  `usd`), (b) stack 1 = Sonnet 5.5 plan + vision, Haiku 5.5 extraction + memory, (c) stack 2 = Sonnet 5.5 plan + vision +
  extraction, Haiku 5.5 memory. Every other call type in both stacks = the committed picks (description + translations
  gpt-6-luna, quiz gpt-4o-mini, tier probe Haiku 4.5, synthesis / faithfulness / chapter_detect as on prod). Re-price per
  call: prompt + cache tokens × the A/B's measured provider-counted prompt-token ratio for that call type (new model vs
  old), output × the measured output ratio for that call type + arm (plan at medium; extraction from round 2 `+rule`
  arms), cache split kept (say so; note the 512-token cache minimum on 5.5). Per call-type breakdown + total per video.
- **Append** a `## Round 2 — <UTC date>` section to `gates/g4-model-ab.md` (round 1 untouched except the median
  re-render) and a `round2` block + rows tagged `"round": 2` to the json. No Picks changes.

## Stop + report
(a) after build + dry run: files, tests, identity check, wire check, estimate (REQUIRED / OPTIONAL); (b) after the live
run: spend + raw per-arm tables; (c) after the classification files exist: the rendered report. Keep `ab-r2/PROGRESS.md`
current so a restart can continue.
