# Round 3 (Kfir 2026-10-12, last before the push word) — briefs for the implementation agents

Repo `/home/kfir/projects/video-insight-engine`, branch `feat/pipeline-1min`, main tree. Follow
`agent-rules.md` (phase-2 section) EXCEPT git: **you do NOT commit** — the coordinator reviews your
working-tree diff (`/review`), you fold the fixes in, the coordinator commits. Skill:
`.claude/skills/backend-python/SKILL.md` + the resources it points to for services. No pipeline
runs, no writes to dev Mongo/Redis/S3/Qdrant, no container restarts, no Langfuse writes, no LLM
spend (unit tests use fakes; the coordinator does the live checks). Heavy commands (full summarizer
suite, pyright on > 10 files) only under the flock in agent-rules.md. Never print secrets.
Already in the tree, uncommitted, the coordinator's (keep them, don't edit): the uploader-tag guard in
`services/video/caption_picker.py` + `tests/test_caption_picker.py` + 3 doc lines.

---------------------------------------------------------------------------------------------------
## A — task 2.5: garbage captions → audio (agent "P")

### Kfir (verbatim)
Before 2.4 a wrong-tag video with ASR garbage went to Whisper; now a fresh run takes the garbage
track. Fix: the tier probe already reads the transcript — add a `transcript_usable` boolean to its
JSON (temp 0; false only for text that is not a coherent transcript of speech, never for odd style or
an unexpected language). When false and the transcript came from captions, take the existing
audio/Whisper path, re-probe, continue. The probe must see the native text before any translation, so
garbage never pays for a translation batch — if today it runs after translation for non-English
captions, move it. Tests: a garbage fixture triggers the fallback; EqaMrzd9nZU and an English video
stay true; the replay stub returns true.

### Facts (coordinator, verified)
- The probe already reads the NATIVE text: `phases/probe.py` `_probe_at_transcript_ready` runs on
  `ctx.clean_text` as soon as `ctx.transcript_ready` is set by `phases/text.py` `run_phase_text`
  (transcript → `transcript_ready.set()` → `probe_for_plan` → plan ∥ memory). Nothing translates before
  it (RAG translation starts at Qdrant indexing after assembly; the output translation is the last
  phase). So nothing moves — say so in the report and keep a test that pins the order (the probe input
  is the untranslated transcript).
- Probe: `pipeline/tier_probe.py` (Haiku 4.5, temp 0, `PROBE_MAX_TOKENS = 80`, 4 s timeout, no retry),
  prompt `src/prompts/tier_probe.txt` (bare JSON, 4 keys), model `models/probe_types.py` `TierProbe`
  (`extra="ignore"`, frozen). Readers: the plan (`probe_for_plan` → `ctx.probe`: playbook + `Hint:`
  line) and the frames branch (`media/visual_tier.py` `await_probe` / `resolve_tier`, ≤ 3 s at Step 6b).
- Live case: TNhaISOUy6Q (golden tech-react-hooks, Fireship, English speech) is tagged `pl`; its only
  ASR is `pl-orig` = Polish ASR of English speech = word salad. Sample (first 25 segments):
  "z UE Crocs i Spears abroad for PSP free and promoting and Windows Phone software or Damage to the
  same Excellent condition and other Games free free Saints Row the third Edition overhaul duplicate-1
  duplicate-1 d Władek pole Dancing with application with download fast-paced download the Free
  Virtual DJ Home Free no download Metin2 xenoxmt2 sp3srodawlkp msvcp100dll ciebie will and unicode
  want2work With The Beatles revolver connection2nd Best of their High english4matura dcd791nt"
  → use it (or a longer synthetic one in the same style) as the garbage fixture.

### Design (coordinator — implement this; deviate only with a reason in the report)
1. `TierProbe.transcript_usable: bool = True` — a missing key (old answers, replay cassettes) means
   usable. The prompt: a `<transcript_usable>` section + the key in `<output_format>`; false ONLY for
   text that is not a coherent transcript of speech (word salad, unrelated fragments strung together,
   mis-recognised gibberish); never false for odd or informal style, disfluencies, lyrics, slang,
   jargon, an unexpected language, a short or sparse transcript, or `(no transcript)`. Keep it short;
   check `PROBE_MAX_TOKENS` still fits the answer (+1 key) with margin. Prompt placeholders unchanged.
2. Fallback, in the text branch after `probe_for_plan` (`phases/text.py`, a helper in a new or
   existing phase module — keep `run_phase_text` short): when `ctx.probe` exists with
   `transcript_usable is False` AND the transcript came from captions (yt-dlp captions, the
   youtube-transcript-api layer, or an S3 blob whose origin is one of those — NOT whisper / gemini /
   metadata) AND the video is not music (`video_data.context.category == "music"`: lyrics / [Music]
   tags would send every song to Whisper) AND the audio fallback is allowed (the chain's own gates —
   `WHISPER_ENABLED`, `GEMINI_API_KEY`, the duration cap) → re-run the transcript phase on the AUDIO
   layers only (Whisper → Gemini, the existing code in `transcript_fetcher`; no S3, no captions, no
   youtube-transcript-api), with the same post-processing as the first pass (cleaning, SponsorBlock,
   language detection → `source_language_code`, `prompt_segments`, `clean_text`), then start a new
   probe on the new text (`start_tier_probe`-style task) and `probe_for_plan` again, then continue to
   plan ∥ memory. ONE fallback per run: never loop (a second unusable answer → continue with what you
   have). Audio fallback not allowed, or it fails → continue with the caption transcript (a result
   beats a failure), logged.
3. The frames branch must not take a tier from a probe that read garbage: in `await_probe` /
   `resolve_tier`, a probe with `transcript_usable is False` counts as no probe (the metadata rule).
4. Provenance: the persisted `transcriptMeta` shows that captions were judged unusable and the audio
   path ran (e.g. an `attempted` entry naming the caption layer + "unusable"; document the value in
   `TranscriptTrail`'s comment and `docs/DATA-MODELS.md`; a new persisted field would need
   `packages/types` too — prefer the existing list). `pipeline.timing` gets the audio pass as its own
   step (e.g. `timed_step("transcript_audio")`) and the re-probe as its own LLM call (same span
   `tier_probe`; fine). The Whisper transcript lands in S3 via assembly as today (`source="whisper"`
   — kept by 2.4's stale rule), so the next run of that video reads it.
5. SSE: reuse the phase events the audio layers already emit (no new `phase` value unless it is added
   to BOTH `VALID_SSE_PHASES` and the web `SSE_PHASE_MAP`, which you do not own — so don't).

### Tests (behaviour, "should … when …")
- the garbage fixture (probe answers false) → audio path taken, the Whisper transcript replaces the
  captions, the probe runs a second time on the new text, plan gets the second probe;
- EqaMrzd9nZU-shaped (Hebrew captions, probe true) and an English caption transcript (probe true) →
  no fallback, one probe call;
- music category / whisper source / audio not allowed / audio fails / second probe also false →
  no fallback or no loop, as specified;
- `TierProbe` without the key → `transcript_usable` True; the replay stub (tests/replay cassettes'
  recorded tier_probe answers) → True — pin it with a test that loads a real cassette answer;
- frames: an unusable probe → metadata tier;
- the probe input is the untranslated native transcript (order pin).

### Files you own
`src/prompts/tier_probe.txt`, `src/services/pipeline/tier_probe.py`, `src/models/probe_types.py`,
`src/services/pipeline/phases/probe.py`, `src/services/pipeline/phases/text.py`,
`src/services/pipeline/phases/transcript.py`, `src/services/transcription/transcript_fetcher.py`
(an audio-only entry, if needed), `src/services/media/visual_tier.py`,
`src/services/pipeline/pipeline_helpers.py` (`TranscriptTrail` comment only), new phase/helper
modules you create, their tests (+ new test files), `tests/replay/**` only if a stub needs it,
docs lines (`docs/SERVICE-SUMMARIZER.md`, `docs/summarizer-workflow.md`, `docs/DATA-MODELS.md`,
`PROJECT-BRIEFING.md`) — the caption-pick lines the coordinator just edited stay as they are.
NOT yours: `translation.py`, `translation_flat.py`, `transcript/english.py` (agent T), `caption_picker.py`.

---------------------------------------------------------------------------------------------------
## B — translation follow-up (agent "T")

### Kfir (verbatim)
2.3 follow-up — the English translation for RAG runs as parallel batches, your ~15 s proposal, plus:
any block that still contains the source script (Hebrew, Arabic, CJK ranges) after its batch is
retried once. Re-run EqaMrzd9nZU in dev and report complete, done, blocks left in Hebrew (must be 0),
translation cost before/after.

### Coordinator's reading (implement BOTH)
1. **Output translation (`pipeline/translation.py` `translate_to_source`) — the "~15 s proposal".**
   Today: ≤ `_MAX_TRANSLATION_BATCH = 200` strings per `translation_full` call,
   `_MAX_CONCURRENT_TRANSLATION_BATCHES` at a time, half-size retry on a failed batch. On EqaMrzd9nZU
   (dev 2026-10-12, Mongo `videoSummaryCache` `_id` 6acd09976e195170d5fcaf94) the output became one
   200-string batch (3,829 in / 4,996 out tokens, gpt-4o-mini, 56.5 s) + a 1.3 s remainder: the whole
   57 s complete→done gap. Make the batches SIZE-bounded (source characters, so a Hebrew/Arabic
   answer stays ≈ ≤ 1.2k output tokens) and run them in parallel (bounded), so the phase wall is
   ≈ one small batch (target ≈ 15 s on that video) at about the same cost (the prompt overhead per
   call is small — measure it from the template and say what extra input it adds). Keep the
   half-size retry and every existing guarantee (nothing dropped, mirror check, partial failure keeps
   the source-language payload as today). Show offline (no LLM) the batch plan the new rule makes
   for that doc's strings (count, max chars) vs today's.
2. **RAG English (`transcript/english.py` `english_blocks`)** — already parallel batches
   (`_BATCH_CHARS = 4000`, `_CONCURRENCY = 4`; dev probe on EqaMrzd9nZU's Hebrew `iw` track: 18.8 s,
   7 calls, 4 of 22 blocks kept Hebrew — count mismatches on unpunctuated ASR text survived the
   half-size retry). (a) Use the same smaller-batch sizing so it gets faster and mismatches rarer (say
   the numbers you chose and why). (b) After its batch (and the half retry), every block whose text
   still contains the source script — Hebrew, Arabic (incl. the supplement + presentation-form
   ranges), CJK (Han, kana, Hangul) — or that kept its source text (failed / mismatch / echo) is
   retried ONCE, one block per call (no count mismatch possible), bounded concurrency; what is still
   left after that keeps its text as today and is counted in one log line. Latin-script sources are
   covered by the "kept its source text" half of the rule.
3. Both keep `stage_name` (`translation_full`, `translation_title`, `rag_translation`) so
   `LLM_TRANSLATION_MODEL` (0102f93) and the cost ledger still see them.

### Tests
- output translation: a big string set → several size-bounded batches started concurrently (the
  wall ≈ one batch with a fake that sleeps), order preserved, nothing dropped; a failing batch still
  half-retries; partial failure as today;
- RAG: a batch answered short (count mismatch) → its blocks retried one by one → 0 left in the
  source script; a block answered still in Hebrew → retried once; still Hebrew after the retry →
  kept + counted, not retried twice; an Arabic and a CJK block detected; an English block with a
  stray Hebrew word? decide (state the threshold you chose — e.g. any source-script letter vs a
  share) and test it; Latin source kept-text → retried.

### Files you own
`src/services/pipeline/translation.py`, `src/services/pipeline/translation_flat.py` (only if a shared
helper belongs there), `src/services/transcript/english.py`, `src/utils/language_utils.py` ONLY to add
a script-range helper if none exists (check `detect_language_by_script` first — prefer reusing it),
their tests (`tests/test_translation.py`, `tests/test_transcript_english.py`, `tests/test_language_utils.py`
appends only), docs lines (`docs/RAG.md`, `docs/SERVICE-SUMMARIZER.md` translation lines,
`docs/llm-cost-model.md` if it describes the batching). NOT yours: the probe / transcript files (agent P).

---------------------------------------------------------------------------------------------------
## Done = (both agents)
Targeted tests green; then under the heavy lock the full summarizer suite once and pyright 1.1.407
on your changed src files; `bash scripts/precommit-python.sh <your .py files>` from the repo root.
Report (≤ 40 lines): what changed per file, the design as built (and any deviation), tests added
(names), suite + pyright counts, output-changing: yes/no (what), and the exact list of paths to commit.
