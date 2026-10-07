# Gate 0 — per-commit review findings (merged; status per finding)

Rule: phase-end `/review` per commit in parallel → one `p0 review fixes` commit. Status: TODO / FIXED / WONTFIX(reason).

## p0.9 5403577 (PROMPT_SOURCE)
1. W possible bug — `config.py:451` guard blocks only known prod names; `ENVIRONMENT=prd/live/demo` enables disk mode. Fix: allow disk only when env ∈ `_DEV_ENVS`; test `prd`. — FIXED · neutral (prod defaults to registry)
2. W test isolation — `test_prompt_registry.py` registry tests read real `PROMPT_SOURCE`; with `PROMPT_SOURCE=disk` exported 4 fail. Fix: autouse fixture forcing `registry`. — FIXED · neutral (tests)
3. W feature gap — disk mode reads via process-lifetime `_read_file_cached`; worker has no reload, uvicorn reload watches *.py only; `transcript_chunker.py:25` caches its own copy → .txt edit needs restart. Fix: bypass cache in disk mode (+ chunker global). — FIXED · neutral (disk mode only)
4. W docs — `docs/OBSERVABILITY.md:74` doesn't mention disk switch / no promptVersions in disk mode. — FIXED · neutral (docs)
5. W atomic commits — formatter churn in the diff (history written; note only). — WONTFIX (lint-staged reformats whole staged files; noted in gate)

## p0.2 cf411bd (description via LLMProvider)
No Critical/Warning. Info: double error log on APIError (`description_analyzer.py:198-203`) → analyzer line to warning; stale docstrings (`:1`, `:210`). — FIXED · neutral (log level + docstrings)

## p0.6 a3bc654 (validator)
1. W bug — `domain_types.py:1263` multi-tag flat fallback now routes through `_validate_block` which PASSES RAW DATA THROUGH on ValidationError → a tag whose flat validation fails gets the whole raw response (e.g. tech gets foodData). Old code skipped the tag. Fix: in the flat-fallback loop validate directly, on ValidationError log + skip; regression test `(["tech","food"], {"foodData": {"steps": []}, "topics": 5})` → no tech. — FIXED · output-changing (multi-tag flat-fallback failure only) · 0 golden affected
2. W possible bug — `_single_tag_block` (`:1236-1243`): wrapper AND top-level fields for the same tag → wrapper dropped silently; reachable via chunked `merge_batch_extractions` (one batch wrapped, one flat). Fix: merge wrapper into flat (wrapper keys win? → union) + warn; test mixed shape. Same in `_validate_multi_tag`. — FIXED · output-changing (mixed wrapper+flat shape only) · 0 golden affected

## p0.4 8d52e23 (registry)
1. W — caps above assembler per-component limits: `language.phrases` 40 vs spot_explorer 25, `language.vocabulary` 50 vs flash_deck 15, `podcast.quotes` 20 vs flash_deck 15. Fix: cap = max assemblerItemCaps of its components (SCALING max only if no limit) + registry test cap ≤ limit. — FIXED · neutral (cap not read until phase 3)
2. W — `narrative.keyMoments` waitsForVisual true (transcript-driven; brief marks only tech.snippets + gaming.highlights; extra tech paths too). Fix: narrative → false; document tech paths. — FIXED · neutral
3. W — siblings double-sourced (registry `siblings` vs `sibling_datasources()` from defaultTabs). Fix: test that default-tab siblings ⊆ registry siblings. — FIXED · neutral (test)
4. W — accessors return cached config objects (shared mutable). Fix: deepcopy. — FIXED · neutral
5. W — toolkit placeholder test tests str.replace only. Fix: snapshot test = pre-commit hardcoded list + fitness.tips. — FIXED · neutral (test)
6. W — `fitness.exercises` (list) ↔ `fitness.timer` (object) siblings contradict "same-shape" note. Fix: reword note to "paths sharing a component" (assembly sibling swap is component-based). — FIXED · neutral (doc note)

## p0.7 7e3d2ce (eval)
1. CRIT — [FIXED: pooled per-video ŝ (df=n(N−1)), tolerance = t₀.₉₅,df·ŝ·√((1+1/N)/m) ≈ 2.12σ̂_mean; MC false-fail 5.2 %; noise.json schemaVersion 2] gate tolerance = one |pass1−pass2| gap vs the 2-pass MEAN → unchanged pipeline fails a metric ~23 % (~50 % across 3). Fix: tolerance = k×noise (k≈2.5) or σ band; scale by √(full/paired) for subsets. — FIXED (uncommitted)
2. W — failed video scores quality 0.0 (not None) → inflates noise, halves baseline → false pass. Fix: None on error; build_noise excludes videos errored in any pass. — FIXED (uncommitted)
3. W — `_eval_api.py:68-77` register-before-login every auth (prod limit 5/h/IP) → 429 kills the window. Fix: login first; register only on login fail + `EVAL_ALLOW_REGISTER`; 429 from register → login. — FIXED (uncommitted)
4. W security — eval.yml job-level env exposes secrets to every step + unpinned pip. Fix: secrets on the probe/run steps only; pinned installs. — FIXED (uncommitted)
5. W — gate accepts `bypassCache:false` reports / apiUrl ≠ noise apiUrl. Fix: GateInputError. — FIXED (uncommitted)
6. W — 401 on SSE GET re-submits (double-paid run). Fix: distinct error → re-auth + re-attach, no re-submit. — FIXED (uncommitted)
7. W low — EVAL_API_URL (a secret) written into eval json / noise.json. Fix: store a label/host. — FIXED (uncommitted)
OUT OF SCOPE / URGENT → user: `.git/config` origin URL embeds a GitHub token → revoke + credential helper.

## p0.5 3914c8f (dataSource validation)
1. W bug — bare-domain dataSource (`"review"`, `"fitness"`) swapped to first path → review pros_cons loses cons; workout loses warmup/cooldown. Fix: no "." + domain is a content tag/modifier → keep. Tests. — FIXED · output-changing · 0 golden affected
2. W bug — finance modifier paths (`finance.costs`, `finance.savingTips`, `finance`) always dropped (not in registry). Fix: let modifier-domain paths through (or register finance). Test. — FIXED · output-changing · 0 golden affected (3 finance-modifier videos, no finance.* tab)
3. W bug — `dataSource: null` dropped; list/dict raises TypeError → whole plan → fallback. Fix: coerce non-str to "" in `_validate_tabs`. — FIXED · output-changing · 0 golden affected
4. W — overview tab with unregistered path dropped (overview ignores data). Fix: skip validation for self-sufficient components (overview). — FIXED · output-changing · 0 golden affected
5. W — all tabs dropped → fallback tabs; tabsDesigned mixes fallback count + drops. Fix: flag `planFallback` or compute from pre-fallback count. — FIXED · output-changing (tabsDesigned/planFallback telemetry only) · 0 golden affected
6. W docs — `docs/SERVICE-SUMMARIZER.md:440-441` drop reasons lack `invalid_datasource` (plan stage, prepended). — FIXED · neutral (docs)

## p0.1 24f8631 + 3ec28eb (timing)
1. W — `llm_provider.py:34,131` `_PROVIDER_ERRORS` misses litellm InternalServerError (Anthropic 500/529), APIConnectionError, BadRequest, NotFound → not in `llmFailures`. Fix: record every exception, keep per-class logging. — FIXED · neutral (telemetry)
2. W bug — failed audio downloads never recorded (`whisper_transcriber.py:120-121`, `gemini_transcriber.py:161-162`). Fix: try/except TranscriptError → record_download ok=False, re-raise. — FIXED · neutral (telemetry)
3. W — Whisper/Gemini calls bypass record_llm_call → timing.llmCalls/costUsd exclude transcription. Fix: hook next to emit_transcription_usage or document exclusion. — FIXED · neutral (telemetry; transcription rows via usage hook)
4. W — `pipeline_runner.py` 593→670 lines (>500). Fix: move timing helpers to a timing module. — FIXED · neutral (refactor: run_timing.py + pipeline_failures.py; runner 466 lines)

NOTE: live golden noise run uses PRE-fix code → rebuild noise.json with `run_eval.py --noise-from <r1.json> <r2.json>` (free) before committing it.

## p0.8 7a88258 (tier-probe A/B) 
1. W — `--rescore` not offline (gather_inputs → docker/yt-dlp/proxy); crashes without --out-json. — FIXED (uncommitted)
2. W — rescore KeyError when a saved row's golden id is no longer live. — FIXED (uncommitted)
3. W — parse_probe discards whole answer on bad format/confidence; prod classifier defaults/clamps. — FIXED (uncommitted)
4. W — errored calls excluded from latency (timeouts invisible to over_3s). — FIXED (uncommitted)
5. W — p95 at n=18 is the max (one outlier) — relabel; commit msg claim overstated. — FIXED (uncommitted) (report)
6. W — report line 3 "tied" contradicts n=18 table (83.3 vs 88.9). — FIXED (uncommitted)
7. W bug — `_tier_probe_inputs.py:75-76` `.slice(15)` keeps opening quote of frameCaption. — FIXED (uncommitted)
8. W — gpt-4o-mini prefix-cache claim unverified. — FIXED (uncommitted) (drop claim; no new spend)

## p0.3 bbf7e7d (replay)
1. W — stubs replace whole `extract_scene_keyframes` / `fill_moment_frames` / `derive_tier` = the code 1a.2/1a.3/1b.1 change → replay can't prove "one 720p download", uC45 frames, tier. Fix: stub at I/O primitives (720p download, ffmpeg/scene detect, S3 put, stream_url) so real orchestration runs. — FIXED (uncommitted)
2. W — `_reselect` discards the real hook's `kept` (hides 1d.4 index→frame risk). Fix: mirror prod (use kept, fall back on exception/empty), assert indices. — FIXED (uncommitted)
3. W security — network guard exempts foreign threads by reusable thread ident; gethostbyname/UDP unpatched. Fix: snapshot Thread objects; patch gethostbyname(_ex). — FIXED (uncommitted)
4. W — `_LAZY_PIPELINE_MODULES` hand-maintained → new lazy modules leak silently. Fix: fail when new src.* modules appear after replay. — FIXED (uncommitted)
5. W — `scripts/replay.py` exits 0 on LLM model mismatches. Fix: count them as divergences. — FIXED (uncommitted)
Info: speed scaling divides CPU time too → floor `--speed` (e.g. ≥0.02).

Replay after fix 1: real scene_extractor/hires/moment-fill/derive_tier run; I/O faked (`media_fakes.py`, cassette schema 2). T1dQhQAm8Tc 239.5→238.5 s (−0.4 %); uC45 171.1→172.6; jMq8 (dev cassette) assembly 10.6→0.0 (moment fill now skips its download under current code) → jMq8 indicative only until a prod recording. Still stubbed (not provable by replay): metadata extract, non-caption transcript fetch, OCR, Qdrant stores, SponsorBlock, status callback, scorer CPU.

Evidence (p0.4/p0.5 agent): all 36 baseline runs ran on p0.5 code; every planned tab used a registered path or `frames`; 0 invalid_datasource drops; 0 bare-domain/finance/null/unregistered-overview dataSources; 0 'Plan tab dropped'/'sibling' log lines. Open note: non-overview `meta.*`/`synthesis.*` dataSources would still be dropped (no golden has one). Decision for Kfir: empty plan after policy/validation → tabsDesigned=0 + planFallback:true (was default-tab count).

Out-of-scope note (p0.1 agent): `llm_retry.py` does not retry InternalServerError/APIConnectionError (Anthropic 500/529) and the runner reports them as UNKNOWN_ERROR → belongs to 1d.5 (retry/fallback rework, D4).

Evidence (p0.6 agent): raw extraction outputs of all 36 baseline runs pulled from Langfuse and replayed through HEAD vs new validator → identical outputs; 0 runs hit the failed-flat-fallback path or the mixed-shape merge. Out of scope: flat (unwrapped) modifier fields are always dropped (e.g. L9aIxRBYJtg finance.costs) → candidate for phase 1/3.

## Totals
23 src/domains.json findings: 16 behavior-neutral, 7 output-changing for some inputs — 0 golden videos affected (verified against all 36 baseline runs) → no golden re-run needed.
