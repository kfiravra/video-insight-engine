# Phase-4 review — recipe for review agents (read-only)

You review ONE phase-4 task's UNCOMMITTED diff (or, at the end, the whole phase as one unit) in
/home/kfir/projects/video-insight-engine (branch feat/pipeline-1min). READ-ONLY: no file edits, no
git mutations, no pipeline runs, no LLM spend, no container commands. Targeted pytest on a few files
is allowed (summarizer: `cd services/summarizer && .venv/bin/python -m pytest <paths> -q`); nothing
heavy (no full suites, no pyright on > 10 files) — other agents use the box's memory.

1. Read the project's review recipe `.claude/commands/review.md` fully and apply it (labels,
   severities, compliance checks; `.claude/skills/backend-python/SKILL.md` for services/ and
   packages/, `backend-node` for api/, `react-vite` for apps/web/).
2. Context: your task's brief (named in your group line), `pipeline-1min-tasks.md` Phase 4 rows
   (incl. the "Checked against the code 2026-10-13" facts), `pipeline-1min-plan.md` §Phase 4,
   `pipeline-1min-context.md` (D1–D29 + the "4.0c docs facts" block + phase-4 run state),
   `agent-rules.md` phase-4 section. The brief + Kfir's task rows are the spec.
3. Review the diff: `git diff -- <paths>` + the new files the coordinator lists; the agent's report
   summary is in your group line. Judge your task's paths only; report an interaction with committed
   work when you see one.
4. Focus: correctness bugs; dependency/lock consistency (pins vs requirement floors, `# via` lines,
   the same pin in CI + Dockerfiles + prod); async mistakes; error handling; behaviour changes the
   report does not declare (model input, emitted events, stored docs, S3 object keys / metadata /
   content types); **`EXTRACTION_PARALLEL=false` byte-identity**; LLM-call contract per model
   (params a model rejects, refusal paths, cost tracking); test gaps for the acceptance criteria;
   dead code; project rules (functions < 50 lines, files < 500 lines — only NEW violations or growth
   caused by the diff). Anything outside the phase-4 tasks → a "g4 question" line, not a finding.

OUTPUT (≤ 60 lines, no preamble): one line per finding, Critical first, then at most 10 Warnings,
no Info:
`[C|W] <label> | <file>:<line> | <issue> | <why it matters> | <concrete fix> | output-changing: y/n`
Then optional `g4 question:` lines. End with one line:
`files reviewed: N; findings: C=<n> W=<n>`.

## Groups (id → task → paths → focus)
- R4a 4.0a → uncommitted: `packages/llm-common/pyproject.toml`, `services/summarizer/requirements.txt`,
  `services/summarizer/requirements.lock`, `services/assistant/requirements.txt`,
  `services/assistant/requirements.lock`, `services/summarizer/src/services/media/s3_client.py`,
  `services/summarizer/src/services/transcription/transcript_store.py`,
  `services/summarizer/tests/test_s3_client.py`, `services/summarizer/tests/test_phase_assembly_transcript_store.py`,
  `scripts/migrate-s3-keys.py`, `scripts/backfill-transcripts.py`,
  `services/summarizer/scripts/backfill-transcripts.py`, `docs/SERVICE-SUMMARIZER.md` → brief
  `brief-4.0a.md`. Agent's evidence: `<scratch>/p40a/` (lock-diff.txt, code-diff.txt, capture-diff.txt
  empty, wire-191/105.jsonl identical, replay3x3.txt, suite-*.txt, pyright-*.txt, smoke-out.txt).
  Check: the locks resolve (every pin satisfies every dependent's range — litellm 1.105.0's floors,
  aiobotocore 3.9.2's botocore range vs botocore 1.43.106 in the summarizer, boto3 ↔ botocore ↔
  s3transfer versions in step), `# via` lines correct, nothing else moved; aiobotocore 3.x client
  semantics vs aioboto3 at each of the 8 call sites (context-manager use, `get_object` body
  read/close, paginators, `delete_objects`, `head_object` errors / `ClientError` codes, presigned URLs
  still on the sync boto3 client, credentials/endpoint/region config passed the same way, no session
  created per call that leaks connectors); tests still exercise the real paths (mocks target the new
  import); the scripts' swap; docs line accurate.
- R4b / R4d / R4.1 / R4.2 / R4.3 → added per task.
