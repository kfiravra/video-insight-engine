# Phase-2 review — recipe for review agents (read-only)

You review ONE group of pipeline-1min phase-2 commits in /home/kfir/projects/video-insight-engine
(branch feat/pipeline-1min). READ-ONLY: no file edits, no git mutations, no pipeline runs, no LLM
spend, no container commands. Targeted pytest/vitest on a few files is allowed; nothing heavy (no
full suites, no pyright on > 10 files, no `tsc -b`) — other agents are using the box's memory.

1. Read the project's review recipe `.claude/commands/review.md` fully and apply it (labels,
   severities, compliance checks; skill files: `.claude/skills/backend-python/SKILL.md` for
   services/, `backend-node` for api/, `react-vite` for apps/web/).
2. Context: `dev/completed/pipeline-1min/pipeline-1min-tasks.md` (Phase 2 rows + the log entries for
   your task id), `pipeline-1min-plan.md` §Phase 2, `pipeline-1min-brief.md` §Phase 2,
   `pipeline-1min-context.md` (decisions D1–D25). The brief is the spec.
3. Review the change of your commit(s) (`git show <sha>`). Other phase-2 work may have changed the
   same code since (committed, or in flight in the working tree): for every finding check the code
   in the working tree and report only what is still present there (or a regression a later change
   introduced into your area). Say when a finding's file has uncommitted changes.
4. Focus: correctness bugs, async/concurrency mistakes (blocking calls in async, unawaited or
   leaked tasks, cancellation, files/handles), error handling, security (secrets in logs/argv,
   injection), data-contract breaks (SSE events, Mongo doc shape, Redis keys/TTL, prompt
   placeholders, `packages/types` vs Pydantic), output changes the commit message does not
   declare, test gaps for the acceptance criteria, dead code left behind, project rules (functions
   < 50 lines, files < 500 lines — report only NEW violations or growth caused by your commits).

OUTPUT (≤ 60 lines, no preamble): one line per finding, Critical first, then at most 10 Warnings,
no Info:
`[C|W] <label> | <file>:<line> | <issue> | <why it matters> | <concrete fix> | output-changing: y/n`
End with one line: `files reviewed: N; still-present findings: C=<n> W=<n>`.

## Groups (id → commits → what)
- R1 refactor → 522362d → pure move: `index_in_qdrant` + `_english_transcript` → `phases/qdrant_index.py`, `log_background_error` → `pipeline_helpers` (verify nothing changed behaviour; 2.3 later edited qdrant_index.py — judge the move itself)
- R2 p2.0 → 58185b8 → plan static half cached 1h (`cache_control` ttl), `pipeline.timing` v2 1h/5m write split + OpenAI cached reads, `scripts/cache_hit_rate.py`, synthesis / enrich_quiz / description_analysis static-first. Known, confirm + fix line: literal doubled braces `{{ }}` sent in `description_analysis.txt` and the plan `<output_schema>`
- R3 p2.3 → 8c124ee → non-English RAG text from the transcript (`transcript/english.py`, `translate_flat` prompt, `rag_translation` span), `index_in_qdrant` awaits nothing. Known, confirm + fix line: `english.py` imports private `_translate_flat_list` / `_is_mirror` from `pipeline/translation.py`; `ctx.audio_path` never set (dead)
- R4 p2.1 → (sha when committed) → emission contract: TabLedger, per-tab `tab_ready` final/provisional/position, provisional Moments at plan-done, always-Moments, final re-emit, `complete.tabIds`, Redis stream reset + TTL at start, leaked-stream script, API relay + `packages/types`
- R5 p2.2 → (sha when committed) → web reducer rules, DB-vs-stream resolution, provisional state, SSE phases, Playwright smoke
- R6 p2.4 → the UNCOMMITTED 2.4 diff in the working tree (`git diff -- <paths>` + new files; the
  coordinator lists the paths) → caption picker (spec + coordinator reading in `brief-2.4.md`):
  legacy-code aliases, spoken language first (yt-dlp `language`, else the single `-orig` auto
  language), manual → auto in it, today's fallback only when nothing matches; stale S3 caption blobs
  yield to the fresh pick; youtube-transcript-api fallback prefers the spoken language. Check the
  rule against every row of the probe table in the brief, the alias on both sides, untranslated-only
  matching, `video_data.language` consistency with the picked track, the S3 rule (whisper / gemini
  kept, failed caption fetch → blob, re-store overwrites, cold unchanged), and test coverage of each.

- R7 2.4 follow-up → the uncommitted uploader-tag guard (`caption_picker.py` `spoken_language`, `tests/test_caption_picker.py`, 3 doc lines) → Kfir round 3: when yt-dlp's `language` tag and the language of the original auto track disagree, the auto track's language is the spoken language. Check: auto-dubbed videos (several `-orig`) keep the tag; no probe-table row changes; the docstring matches.
- R8 2.5 → the uncommitted garbage-caption diff (`brief-round3.md` A is the spec) → `transcript_usable` in the tier probe; a false answer on a caption transcript → audio path, re-probe, continue; frames ignores an unusable probe. Check: the prompt's definition (false only for incoherent text — never style / language / sparse / no transcript), the max-tokens margin, the "captions" test (yt-dlp, transcript-api, S3 caption-origin; not whisper / gemini / metadata), music + gates + one-fallback-only, the re-run's post-processing parity with the first transcript pass (cleaning, SponsorBlock, source language, prompt_segments), provenance (`transcriptMeta`, `pipeline.timing`), SSE phases unchanged, the replay stub → true, cancellation of the first / second probe task.
- R9 2.3 follow-up → the uncommitted translation diff (`brief-round3.md` B is the spec) → output translation in size-bounded parallel batches; RAG English smaller batches + a once-per-block retry of blocks still in the source script or kept as source. Check: nothing dropped / reordered, the half-size retry and mirror check kept, concurrency bounded (the output translation and the RAG translation run at the same time), stage names unchanged, the script ranges, retry-once-only, cost growth per call.
