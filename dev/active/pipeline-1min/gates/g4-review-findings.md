# Phase-4 review findings (recipe `review-p4.md`)

## R4a — 4.0a (LiteLLM 1.105.0; aioboto3 → aiobotocore) — 0 C / 2 W, both folded before the commit
- W1 test gap `services/summarizer/tests/test_s3_client.py` — every S3 test used a MagicMock session; the
  aiobotocore/botocore-dependent paths (`get_json` body read, `exists()` 404 → False, `get_json` NoSuchKey → None) had
  no test on a real client → AioStubber tests on a real `create_client("s3")`.
- W2 `llm_streaming.py:61` + `tests/test_llm_streaming_order.py:3` named LiteLLM 1.91 for the `_keep_arrival_order`
  seam → "1.91–1.105.0 (re-verified 2026-10-13)" (1.105 still sorts streamed chunks by a `time.time()` stamp).
- g4 questions: `file-uploads.md` skill still says aioboto3 (Kfir's file); `_carry_cache_split` is a no-op on 1.105
  (the streamed rebuild keeps the 1h/5m split itself; streamed cost = non-streamed) — keep as a guard or remove;
  `UsageRecord.litellm_version` always "" (`getattr(litellm, "version")` doesn't exist on 1.91 or 1.105 →
  `importlib.metadata.version`); `LITELLM_MODE=PRODUCTION` for local test runs (LiteLLM loads the repo `.env`).

## R4.3 — 4.3 (gate-4 report skeleton) — 1 C / 10 W, all folded before the commit
- C v9 reference used prod medians mixing the two no-frames runs (T1dQ r1, jMq8 r1) → with-frames r2 values, r1 ‡.
- W headline % built on the mixed medians; §3.6 source notes stale after the g3 correction (adb2be5); obsolete
  Q-vision-variance → Q-download-silent; L5 1h break-even (wins from the 3rd plan, not the 2nd); L5 extraction block-1
  saving impossible on Haiku 4.5; L11 block-1 vs whole-prefix sizes; L13 "rough" (n = 1); L7 range omitted Eqa;
  L51eiJEM45U pre-phase-0 v8 record; two rounding slips.

## R4.2 — 4.2 part 1 (docs: v9 flow + stale lines) — 0 C / 10 W, folded before the commit
- API-REFERENCE event table numbering + extraction_progress semantics + non-emitted legacy rows; ARCHITECTURE v8 call
  total; SERVICE-SUMMARIZER false-path chunking lines contradicting extractor.py; broken 4b pointer from
  pipeline_orchestration.py:202; DATA-MODELS categoryConfidence (4.1 removes it) + pre-1b.2 plan input; env comments
  (false-path-only settings); eval runs DO write the S3 raw transcript; Redis cache awaited before `done`, not
  background; LLM_FAST_MAX_TOKENS lines (setting deleted in 4.0d); llm-cost-model token-count citation.

## R4.1 — 4.1 part 1 (sweep) — 0 C / 6 W, folded before the commit
- stream.py docstring flashcards/triage leftover; activate_langfuse.sh / register_prompts.py nonexistent example
  prompt names; INFRASTRUCTURE "only what is listed" vs the two dead LLM_*_MAX_TOKENS passthroughs (removed in 4.0d);
  test_removed_symbols JS-shaped patterns on Python scope + a duplicate test; pipeline_runner docstring order (probe
  inside the text branch); .env.production.example runtime block heading (assistant reads ENVIRONMENT/LOG_FORMAT too).

## R4b — 4.0b (model-keyed guards) — lens B (contracts) 0 C / 4 W
- W double refusal / fallback error rows unmarked → `counts.refusals` overcounts; `counts.fallbacks` mixes D4 +
  refusal fallbacks → stamp `refusedModel` on rows inside `_answer_for_refused`, separate counts (gate 4.0f needs
  "0 refusals without a fallback" computable).
- W docs still say timing v3, no refusal fields / ledger fields → 4.2 part 2 (field list routed).
- W identity `canonical_prompt` dropped `response_format` (model-independent) → hash messages + response_format.
- W refusal rows lack cache tokens (a refused 5.x attempt can write the cache) → spread `_cache_tokens(usage)`.
- lens A (correctness) 0 C / 3 W: refused streamed text1 re-asked on the same model by the split path (+ the chunked
  path's propagate_rate_limit re-raise) → refusal propagated, no re-ask; previous-model re-send cancelled by the
  refused call's outer timeout → TimeoutError treated as transient (attempt 2 on the refused model + D4 hop) → mark
  the call refused, convert; the replay prefill test could not fail → record at the guard. All 7 folded before the
  commit (docs item → 4.2 part 2).

## R4d — 4.0d (A/B + picks + config) — lens A (config + contracts) 0 C / 5 W
- W cache rates stored under prefixed keys but LiteLLM 1.105 passes the bare model to the callback → every lookup
  misses (cache_savings_usd stays 0; the test used the prefixed id); OpenAI cached tokens live in
  prompt_tokens_details.cached_tokens (the callback reads cache_read_input_tokens → 0).
- W llm_retry.py 495 → 502 lines (cap 500). W gemini_transcriber comment "16,384" wrong (single call sends 32,768)
  + stale test docstring. W LLM_CHAPTER_DETECT_MODEL not passed through yet env-changes asks Kfir to check it on prod.
  W frame_analyzer `_vision_provider` docstring "vision stays on the primary (Sonnet)" stale.
- lens B (A/B tool + picks) 0 C / 7 W: plan-low not supported (is_learnable flip on T1dQ ×2 → quiz tab dropped;
  evidence 0.904 vs medium 0.942, noise 1.0) → PLAN AT MEDIUM; description + quiz → gpt-6-luna not supported by the
  rule (quality unknown, B slower) → re-measured with A/B noise floors (≈ $0.01) and decided mechanically; plan scorer
  lacked distinct_sources / evidence flips; report lacked cache columns (A's plan warm from dev runs); answers keyed
  by ordinal ids without a request hash; ceiling per invocation, not cumulative; no tests for cost / resume. All
  folded before the commit (+ lens A's 5).

## R4.1b — 4.1 part 2 (env templates + INFRASTRUCTURE) — 0 C / 5 W, all folded before 5dd60cc
- prod template: OPENAI_API_KEY under OPTIONAL though 4.0d's openai/* defaults need it → REQUIRED; LLM_DESCRIPTION_MODEL /
  LLM_TRANSLATION_MODEL missing (8 touch points) → commented lines; LLM_FAST_PROVIDER blank (prod = openai, the judge
  baseline) → openai; live LLM_EXTRACTION_MODEL pin of a default → commented; .env.example LLM_FAST_MODEL also read by
  the assistant's quiz tool. Questions: .env.example live LLM_FALLBACK_PROVIDER=gemini; DEPLOY.md lacked the OpenAI key.

## R4.2b — 4.2 part 2 (docs) — lens A 0 C / 4 W, lens B 0 C / 7 W, all folded before b14dc91
- A: extraction override = primary-model call (cross-provider fallback P P F, not fast-tier); RAG translation model;
  vision / description re-send timeout recorded as a plain TimeoutError on the refused model (no call_deadline) →
  documented + FINAL-REVIEW code candidate; OpenAI streaming reachable via the warm-up's fallback attempt.
- B: "empty → fast tier / primary" impossible under compose literal defaults; audit_cache_credits blind while provider =
  unknown; briefing MODEL_MAP is the summarizer's (assistant keeps Sonnet 4.6); fast tier = Haiku 4.5 by default,
  gpt-4o-mini via LLM_FAST_PROVIDER=openai; DEPLOY missing-key blast radius; 16,290-token plan block; uC45 median of 2,
  EqaMrzd9nZU = regression video, not a benchmark.

## R4d2 — 4.0d A/B round 2 (tool + report) — 0 C / 10 W, folded before the commit
- spend guard let the ceiling-crossing call go out (check spent + estimate before send); round-1 CLI still used the
  deduplicated `_spent` and no per-call ceiling; memory round-2 repeats byte-identical (temp 0) → noise floor = round 1 vs
  round 2 (mean 0.974, min 0.923); A+rule.0's invented T1dQ visual answer inflates the reference filled_share and 8 of every
  run's T1dQ "lacks"; Picks prose quotes nearest-rank p50s; hard-coded header facts; circular keep-rule test + no tests for
  verdict gates / stack cost; Eqa downstream without memory; round-1 correction wording (A.0 only) + "dup / split" label;
  stack 1 priced on Haiku 5.5 low. g4 question: does the keep-rule "no" stand (rests on jMq8 0.170 vs 0.162, one noise pair,
  and the distance there is mostly removed invented oven steps)?
