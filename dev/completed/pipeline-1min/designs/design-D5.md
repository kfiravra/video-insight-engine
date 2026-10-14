# design-D5 — 1d.6 settings owner + flags (all 1c/1d settings)

Read at HEAD 5bb125c. The wiring agent edits neighbouring lines in the same files
(`LLM_NUM_RETRIES`, `FRAME_TIER_EARLY_CLASSIFIER`, `"classifier"` mapping, `LLM_FALLBACK_PROVIDER`
wording) — not touched by me; every file is re-read right before each Edit.

## Facts that shape the design
- **Vision "None → primary" already works.** `frame_analyzer.py:152`: a falsy
  `get_stage_model("vision")` (None or the compose blank `""`) → caller's `ctx.llm_service.provider`,
  `use_fast_model=False` → `settings.llm_model` (= `anthropic/claude-sonnet-4-6`). Dev compose passes
  blank already (dev vision is Sonnet today); only the config default (Haiku) and the prod compose
  literal (Sonnet) differ. D3 must keep the truthiness check.
- **Chunking knobs.** Live, not passed through today: `CHUNKED_EXTRACTION_THRESHOLD` (900),
  `MAX_TOKENS_PER_BATCH` (50000), `MAX_MINUTES_PER_BATCH` (40), `EXTRACTION_FORCE_SPLIT_CHUNKS` (4),
  `EXTRACTION_PARALLEL_BATCHES` (2 → 6). Dead (config + docs only): `CHAPTER_BATCH_SIZE`,
  `CHUNKED_EXTRACTION_TIMEOUT`, `MAX_CHAPTER_CHARS`, `MAX_TRANSCRIPT_CHARS`. Old enrichment caps are
  not settings (D1's job).
- **Collected config test** = `tests/test_config.py`. `src/__tests__/config.test.py` never runs
  (`pytest.ini` `testpaths = tests`; CI runs plain `pytest`), so its assertion gets folded in.
- **Deleting `EXTRACTION_USE_FAST_FIRST` breaks things until its readers go:** `extractor.py:219-220`
  reads it every extraction and `test_chunked_extraction.py:616-700` patches it (C1); the replay
  driver (`driver.py:141-143`) raises on unknown cassette settings and all 3 cassettes carry it
  (wiring agent / D4); `scripts/eval_extraction_models.py` + its test (1,045 lines) exist only to A/B
  it. So the deletion is a **second stop**, after C1's reader removal.
- **Prod `.env`:** the local copy has none of the newly passed keys, and its `LLM_VISION_MODEL` =
  Sonnet (= blank). `${X:-default}` covers unset + blank → no `.env` edit. Risk: a key ignored in
  the box's `.env` until now becomes LIVE. PyYAML is in `requirements.lock` (compose parses).

## STOP A — `p1d.6` (FIRST; others read these)
### `services/summarizer/src/config.py` (467 lines → ~470)
- `EXTRACTION_PARALLEL_BATCHES: int = 6`. Comment rewritten: extraction runs on Haiku in prod;
  zero 429s recorded, Start-tier headroom > 12× (A19); `pipeline.timing` counts 429s per run →
  back off to 4 if a gate run shows any (D11). Bounds chunked batches now and field groups in v9.
- `EXTRACTION_FORCE_SPLIT_CHUNKS` comment: drop "kept aligned with ×2" (false at 6). Value 4
  unchanged, so the output does not change: with 6 slots the 4 sub-batches run in one round.
- NEW `EXTRACTION_PARALLEL: bool = True`. Comment: v9 field groups run as separate parallel calls
  under EXTRACTION_PARALLEL_BATCHES; false = one call with every requested field. Honest note:
  phase-1 extraction is one call per transcript batch either way (see Q1).
- NEW `FRAME_VISION_PARALLEL: bool = True` (in the vision block). Comment: true = frames go to
  vision in parallel batches (`media/frame_analyzer.py`); false = one call with every frame, the
  pre-batching path kept as the kill switch. Wording matches D3's implementation (constants live in
  D3's module, not in config).
- `LLM_VISION_MODEL: str | None = None` + comment: vision stays on the primary model (Sonnet).
  Frame descriptions drive the moment gallery and OCR, and Haiku was rejected for them on 2026-09-16.
  None → caller's primary. Header block `:54-63`: "only enrichment and the tier probe are pinned".
- DELETE the dead knobs `CHAPTER_BATCH_SIZE`, `CHUNKED_EXTRACTION_TIMEOUT`, `MAX_CHAPTER_CHARS`,
  `MAX_TRANSCRIPT_CHARS` (Q2).
### `docker-compose.yml` + `docker-compose.prod.yml` (`x-summarizer-env`, shared by HTTP + worker)
- New `# Extraction concurrency + chunking` group: `EXTRACTION_PARALLEL: ${EXTRACTION_PARALLEL:-true}`,
  `EXTRACTION_PARALLEL_BATCHES: ${…:-6}`, `CHUNKED_EXTRACTION_THRESHOLD: ${…:-900}`,
  `MAX_TOKENS_PER_BATCH: ${…:-50000}`, `MAX_MINUTES_PER_BATCH: ${…:-40}`,
  `EXTRACTION_FORCE_SPLIT_CHUNKS: ${…:-4}`.
- Under `# Frame extraction`: `FRAME_VISION_ENABLED: ${…:-true}`, `FRAME_VISION_PARALLEL: ${…:-true}`,
  `FRAME_TIER_ENABLED: ${…:-true}`.
- Prod only: `LLM_VISION_MODEL: ${LLM_VISION_MODEL:-}` (was the Sonnet literal; blank = Sonnet) + comment fix.
### `.env.example`
- Per-stage block `:165-174`: vision no longer pinned. `LLM_VISION_MODEL=` line → commented
  `# LLM_VISION_MODEL=anthropic/claude-sonnet-4-6` + "blank = primary model (vision and extraction)".
- New optional block after the Whisper tuning block (commented, same style): the 6 extraction knobs
  + `FRAME_VISION_ENABLED`, `FRAME_VISION_PARALLEL`, `FRAME_TIER_ENABLED`, one comment line each,
  with "back off to 4 on 429s" on the batches line.
### `.env.production.example` (uncommented value + one comment line each, as the file does)
- `:78-79` → `LLM_VISION_MODEL=` with "blank = the primary model (Sonnet), which vision stays on".
  CI's prod-compose validation copies this file, so the blank value is fine.
- `OPTIONAL — tuning`: the same 9 settings with their defaults.
### Docs
- `docs/INFRASTRUCTURE.md`: new `### Pipeline concurrency & flags (2026-10, pipeline-1min)` table
  after the 2026-08 table: the 9 settings + `LLM_VISION_MODEL`. Its sentence says all of them pass
  through both anchors with literal defaults (bool blank = crash loop). The existing
  `FRAME_TIER_ENABLED` row stays and is now true ("passed through").
- `docs/SERVICE-SUMMARIZER.md`: env block → new `# Extraction concurrency` group, plus
  `FRAME_VISION_ENABLED`, `FRAME_VISION_PARALLEL` and `LLM_VISION_MODEL=` in `# Frame pipeline`. Batched
  extraction `:932` "(default 2)" → 6. Config table `:975-982`: drop `CHAPTER_BATCH_SIZE`; batches → 6;
  add `EXTRACTION_PARALLEL` and `MAX_MINUTES_PER_BATCH`.
- `docs/ERROR-HANDLING.md` "Automatic Retries": one line saying rate-limited chunked batches re-run
  sequentially and `EXTRACTION_PARALLEL_BATCHES` (6) bounds concurrency (D11 guard). Only after the
  wiring agent's `:223` edit lands; skip if the coordinator prefers no edit there.
### Tests — `services/summarizer/tests/test_config.py` (14 → ~120 lines)
`Settings(_env_file=None)` with `monkeypatch.delenv(..., raising=False)` for the touched names, so
the host env and `.env` can't leak in.
- `test_should_enable_both_parallel_flags_when_env_is_unset`;
  `test_should_disable_extraction_parallel_when_env_is_false` (+ the same for FRAME_VISION_PARALLEL);
  `test_should_allow_six_parallel_extraction_calls_when_env_is_unset`;
  `test_should_route_vision_to_primary_model_when_no_override_is_set` (`get_stage_model("vision") is None`).
- `test_should_pass_touched_setting_through_compose_with_the_config_default`, parametrized over
  {dev, prod} × the 10 touched settings. It parses `x-summarizer-env` with yaml, asserts
  `${NAME:-raw}` (same name, no renaming), and checks
  `TypeAdapter(field.annotation).validate_python(raw) == field.default`. A blank raw value is allowed
  only for `str | None` fields with default None. Bool fields must have a non-empty literal. The test
  skips if the compose file is missing (container runs).
- `test_should_not_keep_a_deleted_setting_in_config_or_compose`, parametrized over the deleted names
  (the 4 dead knobs now; `EXTRACTION_USE_FAST_FIRST` added at STOP B; the wiring agent's three names
  can join this list, see Q5).
- Fold `test_worker_settings_exist_with_sensible_defaults` in; delete `src/__tests__/config.test.py`
  (Q3).
### Checks (STOP A)
Targeted pytest (`test_config`, `test_chunked_extraction`, `test_transcript_chunker`,
`test_phase_frames`, `test_frame_analyzer`); ruff check + format; pyright on config.py + test.
`docker compose config -q` (dev); prod = the CI recipe `ci.yml:323-341` with a scratchpad env file.
Resolved values come from `docker compose config --format json` for vie-summarizer + worker,
printing only the touched keys (no secrets).

## STOP B — `p1c.5` settings half (after C1 removes the `extractor.py` reads + fast-first tests)
- `config.py`: delete `EXTRACTION_USE_FAST_FIRST` and its comment, and rewrite the
  `LLM_EXTRACTION_MODEL` comment (`:71-79`) as "pins every extraction call and the memory stage
  (D5); None → primary".
- Both compose anchors: delete the line; `.env.example:180-182` comment without the flag.
  `docs/SERVICE-SUMMARIZER.md`: delete `:938` and row `:982`; `:964` → "Extraction:
  `LLM_EXTRACTION_MODEL` (blank → primary)" (these lines also name C3's retry → removed whole).
- DELETE `scripts/eval_extraction_models.py` + `tests/test_eval_extraction_models.py` (tooling for
  the deleted flag; also uses `force_primary_model`, which C1/C3 remove). Q4. Test: the flag joins
  the deleted-settings parametrize.
- Needs: `"EXTRACTION_USE_FAST_FIRST"` out of `tests/replay/build_cassette.py:64` + the 3 cassettes
  (`:27`) or the replay driver raises (wiring agent / D4, or me if granted);
  `phases/extraction.py:159` comment (C3).

## Interfaces provided (nothing needed from others for STOP A)
`EXTRACTION_PARALLEL: bool = True`, `FRAME_VISION_PARALLEL: bool = True` (reader D3),
`EXTRACTION_PARALLEL_BATCHES: int = 6` (reader `extractor.py:633`, unchanged),
`LLM_VISION_MODEL: str | None = None` (via `get_stage_model("vision")`, truthiness).

## Env changes for Kfir (`env-changes.md`)
- **None required.** The literal compose defaults give true / 6 on prod; vision is Sonnet either
  way. Appendix F's `EXTRACTION_PARALLEL_BATCHES=6` line is not needed (it is the default).
- **Pre-deploy check on the box:**
  `grep -E '^(EXTRACTION_|FRAME_(VISION|TIER)|CHUNKED_EXTRACTION_THRESHOLD|MAX_(TOKENS|MINUTES)_PER_BATCH)' .env`
  → expect nothing. Any hit becomes live with this merge (it was ignored before).
- Optional cleanup: the `LLM_VISION_MODEL` line, `EXTRACTION_USE_FAST_FIRST` if present. On 429s at
  gate 1: `EXTRACTION_PARALLEL_BATCHES=4` (D11).

## Risks
- Prod behaviour: chunked extraction goes from 2 to 6 concurrent calls (D11, 429-guarded). Model
  output is unchanged, and no other touched default changes an effective prod value.
- Cassettes pin the recorded `EXTRACTION_PARALLEL_BATCHES: 2`, so replays won't show the 6-wide gain
  unless D4's driver overrides the pin (note for D4).
- Same files as the in-flight wiring agent (no overlapping lines): re-read before every Edit.

## Open questions
1. `EXTRACTION_PARALLEL` has no reader until 3.4: add now (brief 1d.6 + fixed interface; default)
   or with its reader in 3.4 (otherwise a no-op setting in PR 3)?
2. Delete the 4 dead chunking knobs in 1d.6 (default yes) or leave them to the 4.1 sweep?
3. Fold + delete `src/__tests__/config.test.py` (default yes)? The 5 `src/worker/__tests__/*.test.py`
   are uncollectable too — not mine, reported only.
4. Delete `scripts/eval_extraction_models.py` + its test at STOP B (default yes; no owner)?
5. Should the deleted-settings test also guard the wiring agent's `FRAME_TIER_EARLY_CLASSIFIER` /
   `LLM_NUM_RETRIES` (summarizer anchor)? One parametrize entry each.
6. `FRAME_VISION_MAX_FRAMES/TIMEOUT`, `FRAME_OVERSELECT_COUNT`: D3 decides keep/rename/delete; a
   passthrough is then one line per touch point.
7. Stale lines outside my files (report only): `docs/summarizer-workflow.md:139` (vision
   "Haiku-4.5"), `PROJECT-BRIEFING.md:205,417`, the `tests/conftest.py:29-30` docstring.
