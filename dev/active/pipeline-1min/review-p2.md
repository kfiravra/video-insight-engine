# Phase-2 review — recipe for review agents (read-only)

You review ONE group of pipeline-1min phase-2 commits in /home/kfir/projects/video-insight-engine
(branch feat/pipeline-1min). READ-ONLY: no file edits, no git mutations, no pipeline runs, no LLM
spend, no container commands. Targeted pytest/vitest on a few files is allowed; nothing heavy (no
full suites, no pyright on > 10 files, no `tsc -b`) — other agents are using the box's memory.

1. Read the project's review recipe `.claude/commands/review.md` fully and apply it (labels,
   severities, compliance checks; skill files: `.claude/skills/backend-python/SKILL.md` for
   services/, `backend-node` for api/, `react-vite` for apps/web/).
2. Context: `dev/active/pipeline-1min/pipeline-1min-tasks.md` (Phase 2 rows + the log entries for
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

