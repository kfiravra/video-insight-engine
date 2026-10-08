# Phase-1 review — recipe for review agents (read-only)

You review ONE group of pipeline-1min phase-1 commits in /home/kfir/projects/video-insight-engine
(branch feat/pipeline-1min). READ-ONLY: no file edits, no git mutations, no pipeline runs, no LLM spend.

1. Read the project's review recipe `.claude/commands/review.md` fully and apply it (labels, severities,
   compliance checks, skill files: `.claude/skills/backend-python/SKILL.md` for services/,
   `backend-node` for api/, `react-vite` for apps/web/).
2. Context: `dev/active/pipeline-1min/pipeline-1min-plan.md` (Phase 1 rows + conflicts C1–C21),
   `pipeline-1min-context.md` (decisions D1–D25), `designs/decisions.md`. The brief is the spec.
3. Review the combined change of your commits (`git show <sha>` each). Later phase-1 commits may have changed
   the same code — for every finding check the code AT HEAD and report only what is still present at HEAD
   (or a regression a later commit introduced into your area).
4. Focus: correctness bugs, async/concurrency mistakes (blocking calls in async, unawaited tasks, leaked
   tasks/files/handles, cancellation), error handling, security (secrets in logs/argv, injection), data
   contract breaks (SSE events, Mongo doc shape, prompt placeholders), test gaps for the acceptance
   criteria, dead code left behind, project rules (functions < 50 lines, files < 500 lines — report only
   NEW violations or growth caused by your commits).

OUTPUT (≤ 60 lines, no preamble): one line per finding, Critical first, then at most 10 Warnings, no Info:
`[C|W] <label> | <file>:<line at HEAD> | <issue> | <why it matters> | <concrete fix>`
End with one line: `files reviewed: N; still-present findings: C=<n> W=<n>`.

## Groups (id → commits → what)
- G01 proxy-rotation → 7304540 e925e2b (04ccf0b doc) → rotate the proxy exit on YouTube's bot check, exit memory, playlist
- G02 register → 965c618 → register_prompts --label / --dry-run
- G03 p1a.1 → eabe954 → metadata = extract_info only; captions, description, low-res + 720p downloads in background
- G04 p1a.2 → bf618ee → one 720p file per job, stream-URL pass removed, dead settings deleted
- G05 p1a.3 → e18e948 → zero-candidate scene ladder + ffmpeg rc
- G06 p1a.4+1a.5 → c311479 6137c27 → render_transcript markers; English filler removal in basic cleaning
- G07 p1b.1 → 1e8db2f 8010731 203904e 4b273bf 44e4325 9b207bc → temperature plumbing; tier probe module + wiring; classifier retired; LLM_NUM_RETRIES sweep; eval reads tier_probe
- G08 p1b.2 → b7bb9a5 da3a61d 505de70 da14284 → plan rewrite; registry placeholder guard; triage evidence/terms; eval promotion targets
- G09 p1b.3+1b.4 → d61ecc8 4f43993 c62edb1 → memory stage; video_memory renderer; plan ∥ memory wiring
- G10 p1b.5 → 530c3ac db7240a → synthesis_complete merge (web reducer, API relay, types) + early emission
- G11 p1b+1b.6 → 9e58acf 4b453fb → runner split (pure move); chapter_detect gate + memory outline
- G12 p1c.1 → fa81d91 → base_extraction rewrite + extraction_prompt.py
- G13 p1c.2 → a9b2197 f1d8a2e f2648cd → visual annotations renderer, source=visual RAG chunks, inject removed
- G14 p1c.3 → 99bff3b 8793bc6 → user-block cache breakpoint (provider) + extraction cache layout, fast-first removed
- G15 p1c.4 → db56693 → 16 schema files + examples (prompt text)
- G16 p1c.5 → 13a63ff f7ab092 2c26c6a f1922f4 → synthesis-fed retry + count gate removed; EXTRACTION_USE_FAST_FIRST deleted
- G17 p1d.1 → abf8f87 → quiz-only demand-driven enrichment
- G18 p1d.2+1d.7 → 40106f5 afa1f09 → quick_quiz host exclusions + quiz last; requirement-evidence backfill/demotion block
- G19 p1d.3 → 907a910 1e5bfcd → synthesis trim; assembly order (tabs → synthesis ∥ moment fill → save)
- G20 p1d.4 → ec5f68e c94ea68 → vision batching, no upscale, cassette split, scenes-v4
- G21 p1d.5 → 90576a0 124c693 e08e44a cadf9cf e6fc9fa → retry-after/fallback; extraction heartbeats; worker status check + preload; overview RAG; dropped-connection resend
- G22 p1d.6 → 24da9a9 → settings: flags, batches 6, passthroughs, vision default
- G23 p1d.8 → 00a68bc 2673fd4 → eval-user versions (API) + eval runs skip shared caches (summarizer)
- G24 p0.10/docs+eval → 501d7a4 (skip — docs only) — NOT reviewed
