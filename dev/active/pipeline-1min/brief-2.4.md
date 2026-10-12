# 2.4 — caption picker (Kfir 2026-10-12, gate-2 decision 4) — brief for the implementation agent

Repo `/home/kfir/projects/video-insight-engine`, branch `feat/pipeline-1min`, main tree. Follow
`agent-rules.md` (phase-2 section) EXCEPT the git part: **you do NOT commit** — the coordinator
runs `/review` on your diff, you fold the fixes in, the coordinator commits 2.4. Skill:
`.claude/skills/backend-python/SKILL.md` (+ the resources it points to for services). No LLM
spend, no pipeline runs, no writes to dev Mongo/Redis/S3. You MAY run the metadata-only probe
(below) through `docker exec` to check the new pick on real yt-dlp data (vie-summarizer runs
`--reload`, so it sees your code).

## Spec (Kfir, verbatim)
- alias YouTube's legacy codes before matching: iw→he, ji→yi, in→id;
- choose the language first, then manual over auto: spoken language = yt-dlp's language field,
  else the language of the original (non-translated) auto-generated track; take a manual track in
  that language, else the auto track in that language; a manual track in another language is never
  chosen over the spoken language. Today's fallback only when nothing matches.

## Reading of the spec (coordinator)
- Spoken language: `info["language"]` normalized + aliased (`en-US` → en, `iw` → he). Absent or
  unsupported → the language of the ORIGINAL auto track = the auto keys ending `-orig` (yt-dlp adds
  `<lang>-orig` only for an ASR track). Several distinct `-orig` languages (an auto-dubbed video:
  one ASR per dub audio track) without a language field → no signal.
- Match a caption key by its aliased primary subtag (`iw-orig` → he, `en-US-cvfXDfbeED0` → en,
  `pt-BR` → pt). Only UNTRANSLATED json3 formats count (no `tlang` in the URL — `_json3_url`
  already does this; one key can list formats from several source tracks, e.g. a dubbed video's
  `en` key lists the ar→en translation first). A machine translation INTO the spoken language is
  not "a track in that language".
- Manual in the spoken language → else auto (untranslated) in the spoken language → else today's
  code (`resolve_video_language` waterfall + `_pick_subtitle_url`) unchanged.
- `video_data.language` = the spoken language when the rule picked a track (it is what the
  transcript is in; the transcript phase reads it as the source language). Fallback → today's value.
- The aliases belong in `normalize_language_code` (`src/utils/language_utils.py`) so every caller
  gains them (today `iw` → None). Check `yi` / `id` are supported codes; matching must not depend on
  a language being in `SUPPORTED_LANGS` beyond what normalization already requires — say what you chose.

## Also in 2.4 (coordinator decisions — without them the fix never reaches a cached video)
1. **Stale S3 transcript blobs.** `videos/<id>/transcript.json` is keyed by video id only and is
   read first (`transcript_fetcher.fetch_transcript`), so every video processed before 2.4 keeps
   the old track forever (dev: vOkkWuEwUmc's blob is the Dutch manual track; EqaMrzd9nZU's is the
   English auto-dub ASR labelled `he`). The caption fetch already runs on every job
   (`metadata.py` `caption_task`; the transcript phase runs after it), so the fresh pick is in hand
   at S3-read time at no extra YouTube cost. Rule: record the picked caption key (+ kind) in the
   stored blob (`RawTranscript` optional fields; the store call is `phases/assembly.py` ~:407);
   a cached blob of caption origin (any source except whisper / gemini — audio transcriptions are
   picker-independent) is STALE when fresh captions were fetched this run (`video_data.subtitles`
   non-empty) and its recorded key differs from `video_data.caption_lang` (pre-2.4 blobs record
   none → stale once, then re-stored). Stale → log + leave a trace in the transcript trail (use what
   `transcriptMeta` already carries if possible; a new persisted field = `packages/types` +
   `docs/DATA-MODELS.md` too) → the yt-dlp layer takes the fresh captions, assembly re-stores
   (verify it overwrites). Caption fetch failed → the blob is used as today. `cold` unchanged.
2. **youtube-transcript-api fallback** (`transcription/transcript.py` `_select_track`, English-
   first today): when the spoken language is known, manual in that language → generated in that
   language → today's order. Pass only the SPOKEN signal (not today's waterfall result, which can be
   `ar` for an English video with community subtitles) — e.g. a `spoken_language` on `VideoData`.

## Probe (dev, yt-dlp metadata only, 2026-10-12) — use these SHAPES for the tests (synthetic dicts)
Script: `<scratch>/probe/caption_probe.py`, output `<scratch>/probe/probe.out`
(`<scratch>` = `/tmp/claude-1000/-home-kfir-projects-video-insight-engine/afdac26f-4bd5-4149-b4a2-394ff4ee0f52/scratchpad`).
Run: `docker exec -i -w /app vie-summarizer python - <ids…> < <scratch>/probe/caption_probe.py`.

| video | `language` | manual keys | untranslated auto keys | today | expected |
|---|---|---|---|---|---|
| EqaMrzd9nZU (Hebrew) | iw | — | iw, en-US-orig, en-US, iw-orig | auto en-US-orig (dub ASR) | auto iw…, language he |
| vOkkWuEwUmc (golden travel) | en-US | nl, en-US, fr, de, it, ja, es, sv | nl-nl, en-orig, en, fr-fr… | manual nl | manual en-US |
| v8KaQr0MhjE | en | ar, en, es | en, ar-ar, en-en, en-orig, es-es | manual ar | manual en |
| eo5XndJaz-Y / e-P5IFTqB98 / Iuv6hY6zsd0 / E7CwqNHn_Ns | en | ar, …, en, … (10–43) | en, ar-ar, …, en-orig | manual ar | manual en |
| yv64abAJvEA | en-US | pt, es-419 | en-orig, en, pt-pt | manual pt | auto en |
| T1dQhQAm8Tc / uC45_4nnEAI (21 dub ASRs) | en-US | — | ar-orig, ar, bn, …, en, …, en-orig, … | auto en | auto en |
| 2pp17E4E-O8 / jMq8lEu-of0 / f3DfJxvkN-8 | en / en-US | en-qlPKC2UN_YU / en-US-cvfXDfbeED0 / en-US | en-orig, en, … | manual (same) | unchanged |
| wCkLNqy5OHE / Jru5B044HOs | en | — | en-orig, en | auto en-orig | unchanged |
| TNhaISOUy6Q (golden react-hooks) | pl | — | pl-orig, pl | auto `en` = machine translation of the pl ASR | auto pl (the rule) |

TNhaISOUy6Q is a known limitation, not a bug of the rule: the uploader set Polish, YouTube ran
Polish ASR on English speech, the `pl` track is garbage (and today's `en` pick is a machine
translation of the same garbage). Its dev S3 blob is a Whisper transcript (kept by rule 1). Do not
special-case it; the coordinator reports it. Add tests for: no language field + one `-orig` →
that language; no field + several `-orig` → today's fallback; field present but no track in that
language (e.g. `da` with only `en-orig`) → today's fallback; alias on both sides (`he` field vs
`iw` key, `iw` field vs `iw-orig` key).

## Files you own
`services/summarizer/src/services/video/youtube.py`, `src/utils/language_utils.py`,
`src/services/transcription/transcript.py`, `src/services/transcription/transcript_fetcher.py`,
`src/services/transcription/transcript_store.py`, `src/services/pipeline/phases/assembly.py`
(the transcript store call only), `src/services/transcription/transcript_meta.py` (if needed),
their tests (`tests/test_youtube_lang.py`, `test_youtube_service.py`, `test_language_utils.py`,
`test_transcript_fetcher*.py`, `test_transcript_store.py`, `test_phase_assembly_transcript_store.py`,
`test_transcript_phase_language.py`, new test files as needed), and the docs lines that describe
track selection or the S3 transcript cache (`docs/SERVICE-SUMMARIZER.md`, `docs/summarizer-workflow.md`,
`docs/DATA-MODELS.md`, `PROJECT-BRIEFING.md` — grep). NOT `src/config.py` (the coordinator is
editing `_STAGE_TO_SETTING`).

## Done =
Targeted tests green; then under the heavy lock the full summarizer suite once
(`cd services/summarizer && .venv/bin/python -m pytest -q`) and pyright 1.1.407 on your changed src
files; `bash scripts/precommit-python.sh <your .py files>` from the repo root. Report (≤ 50 lines):
what changed per file, the rule as implemented (incl. the alias / unsupported-language choice),
the probe re-run (old pick → new pick per video), tests added (names), suite + pyright counts,
output-changing: yes (what), and the exact list of paths to commit.
