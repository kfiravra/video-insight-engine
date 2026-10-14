# Data Models

MongoDB collections and schemas.

---

## Overview

### System Cache (Shared)

| Collection | Purpose |
|------------|---------|
| `videoSummaryCache` | One summary per YouTube video (MongoDB + Redis cache layer) |
| `systemExpansionCache` | One expansion per section/concept |

### User Data (Per-User)

| Collection | Purpose |
|------------|---------|
| `users` | Accounts |
| `folders` | Organization hierarchy |
| `userVideos` | User's video library |

---

## Legacy: ContentBlock Types (Removed)

The block-based content system was replaced by the composable output system (v2) with component-addressed tabs. See the `assembledTabs` field in videoSummaryCache for the current data shape.

---

# Content Tag System (v2)

The plan determines content tags from the video metadata and the full transcript (steered by the tier probe's domain); reconcile corrects the tabs at plan-done. Tags drive schema selection, tab layout, the extraction field groups, and the quiz.

### 14 Primary Content Tags + 2 Modifiers

Enrichment is the quiz only (`enrich_quiz.txt`, pipeline-1min 1d.1): it runs for the `domains.json` `quizEnrichment.quizDomains` (learning, language, tech, science), and only when the plan has a quiz host (`enrichment.needs_quiz`); the domain sets the quiz's flavor line. The per-domain `enrich_*.txt` prompts, flashcards and scenarios are gone.

| ContentTag | Domain Schema | Quiz |
|------------|---------------|------|
| `learning` | `schemas/learning.txt` | yes (when planned) |
| `tech` | `schemas/tech.txt` | yes (when planned) |
| `fitness` | `schemas/fitness.txt` | - |
| `food` | `schemas/food.txt` | - |
| `music` | `schemas/music.txt` | - |
| `travel` | `schemas/travel.txt` | - |
| `review` | `schemas/review.txt` | - |
| `project` | `schemas/project.txt` | - |
| `language` | `schemas/language.txt` | yes (when planned) |
| `science` | `schemas/science.txt` | yes (when planned) |
| `gaming` | `schemas/gaming.txt` | - |
| `news` | `schemas/news.txt` | - |
| `podcast` | `schemas/podcast.txt` | - |
| `sport` | `schemas/sport.txt` | - |
| `narrative` | `schemas/narrative.txt` | Modifier only |
| `finance` | `schemas/finance.txt` | Modifier only |

### Interactive Component Types

Component props live in `packages/types/src/vie-response.ts`. The **concept canvas**
(learning & science domains) uses a typed, grouped graph (assembled by
`assemble_concept_canvas`; see [SERVICE-SUMMARIZER.md](./SERVICE-SUMMARIZER.md#concept-canvas-schema-learning--science-domains)):

```typescript
// Relation drives EDGE STYLE, never color (see DESIGN.md):
//   causes / requires  → solid line + arrowhead (directional dependency)
//   contrasts          → dashed line (opposition / trade-off)
//   partOf / relatesTo → dotted line (loose association)
export type ConceptRelation = 'causes' | 'contrasts' | 'requires' | 'partOf' | 'relatesTo';

export interface ConceptConnection {
  to: string;                    // EXACT name of another concept in the same array
  type: ConceptRelation;
}

export interface ConceptItem extends FrameEvidence {
  name: string;
  emoji: string;
  definition: string;
  example?: string;
  analogy?: string;
  group?: string;                // thematic cluster / lane (assembler derives/defaults one)
  timestamp?: number;            // seconds into the video — drives frame evidence
  thumbnailUrl?: string;         // injected by inject_frame_thumbnails on timestamp match
  connections: Array<string | ConceptConnection>;  // bare string = legacy → coerced to relatesTo
}

export interface ConceptCanvasProps {
  concepts: ConceptItem[];
  groups?: string[];             // ordered, de-duped lane order (added in v6)
  onSeek?: (seconds: number) => void;
  // videoId / nextTab / onNavigateTab remain as optional nav props
}
```

#### Notable TabEntry / item fields (2026-08 additions)

Mirrored in `packages/types/src/vie-response.ts` and the summarizer's Pydantic
models (`domain_types.py` — keep them in sync, undeclared fields silently drop
on `model_dump`):

- `TabEntry.attachments?: TabAttachment[]` — secondary components rendered around the primary (`stat_banner`, `tip_callout`, `summary_header`, `diagram_card`, `frame_strip`, `quick_quiz`).
- `TabEntry.degradedFrom?: string` — original component name when the assembly demote ladder downgraded the tab.
- `FrameEvidence.s3Key?: string` — stable S3 key per frame; lets the api re-sign expired presigned URLs on serve (`refresh-frame-urls.ts`).
- `SpotItem.pronunciation?: string` — travel spots.
- `MomentItem` carries frame fields (`frameCaption`, `frameEvidence`, `frameOcr`, `frameSceneType`, `s3Key`) filled by assembly frame injection + `moment_frame_fill`.
- `SSETabReadyEvent` (`packages/types/src/api.ts`, extends `TabEntry`) — `position?: number` = index in the response's tab order, a sort key (exact when `final`, an estimate before); `final?: boolean` = the tab's last emission this run (one per tab of the response); `provisional?: boolean` = the plan-done Moments placeholder, replaced by id; `replaces?: string` = on the first emission of the tab that takes the provisional's place under another id, the provisional id (a corrected `final` can follow for an id — the last final wins). `SSECompleteEvent.tabIds?: string[]` = the final tab order. Contract: `services/summarizer/src/services/pipeline/tab_ledger.py`, API-REFERENCE.md#tab_ready (pipeline-1min 2.1).
- A `moment_track` tab always exists when a moment source has entries (2.1): without a planned one, assembly adds `key_moments` ("Key Moments") from the memory outline → YouTube chapters → description timestamps; it is stored, cached, RAG-indexed and translated like any tab. It only adds: it counts neither toward the minimum-3 fallbacks (a two-tab response still gets Key Info) nor toward the rich-moment-grid gate that skips the auto "Visual Moments" filmstrip, and the minimum-3 fallbacks no longer build a chapters moment_track of their own.

#### Component density gates (`densityGates`) and domain policy

`packages/shared/src/config/domains.json` carries a `densityGates` map
(`Record<string, { min; max; chars }>`, typed in
`packages/shared/src/config/index.ts`). It is **advisory planner guidance only** —
rendered into the plan prompt as `{density_gates}` to steer how many items the
LLM emits per component. It is **not** an enforcement layer: the summarizer's
per-assembler hard caps are independent. Edit `domains.json` to change either the
planner-selectable `components` list or these gates.

The same file also carries **enforced** policy blocks (helpers in
`services/summarizer/src/shared_config/domain_config.py`):

- `domainRequirements` — per-domain `required` / `max` / **`forbidden`** component lists (e.g. `quiz_arena` is forbidden outside learning/language/tech/science). Enforced at plan post-validation AND at assembly.
- `playbooks` — keyed `"<domain>:<format>"` (currently `gaming:unboxing`, `review:unboxing`): `required`/`preferred`/`forbidden`/`planGuidance`. Merge via `effective_requirements()`: forbidden = union, required = playbook overrides domain, max always from domain.
- `visualCriticality` — adaptive frame-pipeline tiers (`high`/`standard`/`low` with per-tier vision budgets, `highDomains`/`lowDomains`/`highTitleKeywords`).

### Legacy: VideoContext

Still present in older documents for backward compatibility:

```typescript
interface VideoContext {
  category: string;            // Detected category (used as triage fallback)
  youtubeCategory: string;     // Raw YouTube category
  tags: string[];              // Raw tags from YouTube
  displayTags: string[];       // Cleaned for UI display
}
```

A failed plan or plan confidence < 0.6 → the category's default tabs (`plan._build_fallback_plan`; the category hint is a confident tier probe's domain, else this metadata category). The plan reads metadata + the `[m:ss]`-marked transcript.

---

# System Cache Collections

## videoSummaryCache

One row per video **version** (`version`, `isLatest` below); the served row is shared across all users.

```javascript
{
  _id: ObjectId,

  // YouTube identification
  youtubeId: string,              // "dQw4w9WgXcQ" — repeated on every version row (not unique)
  url: string,

  // Metadata
  title: string,
  channel: string | null,
  duration: number | null,        // seconds
  thumbnailUrl: string | null,
  language: string | null,        // ISO 639-1 ("en", "es", etc.)

  // Video context (classification + tags)
  // Note: Now includes contentTags/primaryTag from plan stage.
  // The legacy `category` field is kept for backward compat but
  // contentTags is the authoritative classification source.
  context: {
    category: string,                // Legacy category (fallback only)
    contentTags: string[],           // From plan stage: ["learning", "tech"]
    primaryTag: string,              // First content tag
    youtubeCategory: string,
    tags: string[],
    displayTags: string[]
  } | null,

  // Processing state
  status: "pending" | "processing" | "completed" | "failed",
  errorMessage: string | null,
  errorCode: string | null,       // "NO_TRANSCRIPT", "VIDEO_TOO_LONG", etc.
  retryCount: number,             // Default: 0

  // Legacy, never written by the current pipeline. Transcript text lives in
  // S3 at the deterministic key videos/<youtubeId>/transcript.json
  // (rawTranscriptRef is NOT set for pipeline rows — see its comment below):
  // { youtube_id, fetched_at, source, language, segments: [{text, startMs, endMs}],
  //   caption: { lang, kind } | null }  — caption = the yt-dlp track a "ytdlp" blob holds
  // (2.4; absent on older blobs). A caption-origin blob whose track differs from a later
  // run's pick is skipped as stale and overwritten.
  transcript: string | null,
  transcriptSegments: [{
    text: string,
    startMs: number,           // Milliseconds
    endMs: number
  }] | null,

  // Transcript provenance — written ONCE per run by the pipeline runner right
  // after the transcript+frames phase, for every run that REACHES the transcript
  // phase — successful and failed fetches alike (a metadata-phase failure, e.g.
  // VIDEO_TOO_SHORT, carries no block).
  transcriptMeta: {
    outcome: "ok" | "failed",
    source: "s3" | "ytdlp" | "api" | "proxy" | "whisper" | "gemini" | "metadata" | null,  // null on failure
    type: "manual" | "auto-generated" | "asr" | "metadata" | "cached" | null,
    origin: "ytdlp" | "api" | "proxy" | "whisper" | "gemini" | "metadata" | null,          // s3 rows only: layer that produced the cached blob
    captionTrack: "manual" | "auto-generated" | null,   // track THIS run's metadata phase picked (null = no usable json3 track)
    captionLang: string | null,                          // matched caption key ("ar-SA", "en-orig", legacy "iw"); since 2.4 a key in the spoken language whenever an untranslated track in it exists — the fallback pick can still differ from the video language (incl. a machine translation, its last resort)
    captionFetchError: "http_429" | "http_403" | "http_<n>" | "request" | "parse" | "empty" | null,  // "parse" = json3 body unparseable → captions discarded, chain continues
    captionApiSkipped: boolean,                          // youtube-transcript-api skipped by the Redis caption-429 marker
    attempted: string[],                                 // layers that RAN and FAILED before `source`, in order: s3 | s3-stale | ytdlp | api | proxy | whisper | gemini | <source>-unusable — s3-stale = the S3 blob held another caption track than this run's pick (skipped, re-stored); <source>-unusable (ytdlp-unusable, api-unusable, proxy-unusable, s3-unusable, whisper-unusable, …) = one per tier-probe answer that judged the transcript that source served not a coherent transcript of speech (`transcript_usable: false`, 2.5). An audio `source` (whisper / gemini) after a caption one = the audio path replaced it; `source` still that layer = the run kept it (music, not captions, no audio layer allowed, or the audio pass failed — its failed layers follow the entry). A run that went on with an unusable answer (no replacement, or the second answer unusable too) planned without a probe
    segments: number | null,
    chars: number | null,
    fetchWallMs: number | null,                          // wall time of the fetch chain inside the transcript phase (frames run in parallel), incl. the audio re-run after an unusable caption transcript; set on failed rows too
    errorCode: string | null                             // failed rows: TranscriptError code (NO_TRANSCRIPT, RATE_LIMITED, …), UNKNOWN_ERROR (unexpected exception) or CANCELLED (producer torn down mid-fetch)
  } | absent,

  // Per-run timing — written by pipeline_orchestration (run_timing.persist_run_timing) when the phases finish OR
  // fail (dotted `$set` after assembly's save; absent on Redis-served rows).
  // Eval-user runs (evalRun) write it too — it is how benchmarks are read.
  // Offsets are ms since the run started. Mirrored (minus llmCalls / llmFailures)
  // onto the Langfuse trace metadata; each phase is also a Langfuse span.
  "pipeline.timing": {
    version: 4,                     // 2: the 1h/5m cache-write split on llmCalls (pipeline-1min 2.0); 3: ttftMs on llmCalls (3.4); 4: refusals (4.0b) — "Refusal" llmFailures rows, refusedModel stamps, counts.refusals / refusalFallbacks / refusalsUnanswered, fallbacks = cross-provider + model swaps only, costUsd incl. refused calls
    startedAt: Date,
    totalMs: number,
    phases: [{ name: string, startMs: number, endMs: number, wallMs: number }],  // metadata, transcript_frames (phase 2: text, transcript, transcript_audio = the audio re-run after the tier probe judged the captions unusable (2.5; that run also has a second tier_probe llmCall), plan, memory, frames, frames.* sub-steps incl. frames.tier_wait), visual_inject (name kept: the annotations render), extraction, split_extraction (EXTRACTION_PARALLEL: the group-extraction member of phase 2, which then also spans transcript_frames; extraction = plan-done → last group; quiz_call = the quiz call it started once the quiz's groups landed), assembly (moment_fill, synthesis, enrichment = the quiz, run inside it), translation
    milestones: { metadataMs?, synthesisCompleteMs?, firstTabReadyMs?, firstAssembledTabMs?, completeMs?, doneMs? },  // firstTabReadyMs = first tab_ready of any kind — since 2.1 the provisional Moments tab at plan-done; firstAssembledTabMs = first non-provisional tab_ready (the first tab assembly built)
    llmCalls: [{ feature, span, model, responseModel, startMs, wallMs, inputTokens, outputTokens,
                 cacheReadTokens,     // Anthropic cache_read_input_tokens, or OpenAI cached input (prompt_tokens_details.cached_tokens; 0 for OpenAI before v2)
                 cacheWriteTokens, cacheWrite5mTokens, cacheWrite1hTokens,  // the write split by cache lifetime, when the provider reports it
                 costUsd, attempt,
                 fallbackUsed,        // answered by a fallback: the cross-provider one (D4), the previous model after a refusal, or a provider-side model swap
                 finishReason,
                 ttftMs,              // ms to the first streamed chunk — the split extraction's warm-up call (span extraction_text1); null otherwise
                 refusedModel?, refusalCategory? }],  // v4, only on the previous model's answer to a refused call: the model that refused + its stop_details category (always null on LiteLLM 1.105, which drops stop_details)
                 // Incl. Whisper/Gemini transcription rows (span "transcription:openai|google")
    llmFailures: [{ feature, span, model, startMs, wallMs, attempt,
                    error,            // exception class name, or "Refusal" — a declined answer (HTTP 200; llm_common.refusal)
                    rateLimited,
                    refusedModel?,    // v4: any row recorded during a refused call's one re-send on the previous model (its refusal, error or timeout) — a timeout only under call_deadline (LLMService calls); a re-send cancelled by frame vision's wait_for or the description's 30 s cap is a plain TimeoutError row under the refused model, without refusedModel
                    // error "Refusal" rows only — what the declined answer was billed:
                    refusalCategory?, inputTokens?, outputTokens?, cacheReadTokens?, cacheWriteTokens?, cacheWrite5mTokens?, cacheWrite1hTokens?, costUsd? }],
    downloads: [{ kind: "lowres" | "720p" | "audio", purpose: string, startMs, wallMs, bytes: number | null,
                  mbPerSec: number | null, ok: boolean,
                  // yt-dlp video downloads only: the picked rendition, the clients + proxy exit that delivered it, why it failed
                  format?: string | null, height?: number | null, clients?: string | null, proxyExit?: string | null, error?: string | null }],
    costUsd: number,                // Σ costUsd over llmCalls + llmFailures — refused calls included (v4)
    counts: { llmCalls, llmFailures, retries, rateLimited,
              fallbacks,            // answers with fallbackUsed and no refusedModel: the cross-provider fallback + provider-side model swaps
              refusals,             // v4: calls refused — "Refusal" rows without refusedModel
              refusalFallbacks,     // v4: refused calls the previous model answered — llmCalls with refusedModel
              refusalsUnanswered,   // v4: refusals − refusalFallbacks
              outputTokens, cacheReadTokens,  // Σ over llmCalls only (refused answers excluded)
              downloadBytes, tabsPlanned, tabsAssembled, tabsEmitted,  // tabsEmitted = distinct tab ids streamed non-provisional (a provisional tab replaced by id counts once; one withdrawn or replaced under another id never counts; an assembled tab withdrawn later still counts)
              truncated }  // answers that stopped at max_tokens (finish_reason=length); DONE log line `llm truncated=N`
  } | absent,
  // The usage ledger (`llm_usage`, one row per LiteLLM call) tags the same events:
  // refusal / refusal_category on a declined answer's row, fallback_from = the refused
  // model on the previous model's row — llm-cost-model.md §Schema.

  // Processed summary (legacy v1 format — kept for backward compat,
  // new pipeline populates assembledMeta/assembledTabs instead)
  summary: {
    tldr: string,
    keyTakeaways: string[],

    chapters: [{
      id: string,                 // UUID
      timestamp: string,          // "03:45"
      startSeconds: number,
      endSeconds: number,
      title: string,
      originalTitle: string,      // Creator's original chapter title (if any)
      generatedTitle?: string,    // AI-generated title (if different)
      isCreatorChapter: boolean,  // True if from YouTube chapters
      content: ContentBlock[],    // Dynamic content blocks with blockId
      transcript: string | null,  // Sliced transcript text for this chapter
      summary: string,            // Legacy: kept for backward compat
      bullets: string[]           // Legacy: kept for backward compat
    }],

    concepts: [{
      id: string,                 // UUID
      name: string,
      definition: string | null,
      timestamp: string | null,
      chapterIndex: number | null, // Per-chapter assignment
      aliases: string[]            // LLM-provided short forms for matching
    }]
  } | null,

  // ─── Pipeline v2 fields (triage-based) ───

  // Triage result
  triage: {
    contentTags: string[],          // ["learning", "tech"]
    modifiers: string[],            // ["narrative", "finance"]
    primaryTag: string,             // First content tag
    tabs: [{                        // LLM-designed tab layout
      id: string,
      label: string,
      emoji: string,
      dataSource: string            // e.g., "learning.keyPoints"
    }],
    confidence: number
  } | null,

  // The summarizer's own copy of each stage's answer (allowlisted $set in
  // save_structured_result): pipeline.triage = the triage_complete payload —
  // the fields above + userGoal, contentFormat (the tier probe's), tab briefs
  // ({what, where[], expect}), evidence ({has_steps, has_ingredients, … : bool}
  // = the reconciled view every reader shares — true when the plan OR the
  // video memory said true, false only when BOTH answered false, absent
  // otherwise (neither said true and one was silent or failed); has_code
  // turns true at frames-done when the screen shows code; drives conditional domain
  // requirements, the quiz gate, <video_memory> and the assembly backfill
  // check), planEvidence / memoryEvidence (each reader's own answers;
  // memoryEvidence null = memory failed) and terms (≤12 canonical spellings);
  // pipeline.reconcile = every tab decision of the plan-parse guard and the
  // plan-done / frames-done reconcile, in order: [{tabId, component, from
  // (the planned dataSource; null for a tab reconcile added or would have
  // added), to (the new dataSource or null), toComponent? (a demotion's rung),
  // reason, action, stage: "plan_parse" | "plan_done" | "frames_done",
  // evidence: {key, plan, memory} (plan-done) | {key, screen} (frames-done) |
  // null}]. Actions: repointed, kept_for_moment_fallback, demoted, dropped,
  // deferred_to_frames (a has_code tab — or tech's required code_playground
  // when no tab was planned for it — waits for the screen check),
  // kept_screen_code, backfilled (required_missing), not_backfilled (a
  // required component its doubly-false key rules out; tabId = the defaultTab
  // it would have added, "" when a live tab holds that id), filled
  // (under_min), moved_last (quiz_policy).
  // Reasons: evidence_false, screen_code, required_missing, under_min,
  // quiz_policy, plus the drop reasons — duplicate, over_max, quiz_domain,
  // evidence_false, inactive_domain — which droppedTabs lists as
  // reconcile_<reason>;
  // pipeline.extraction (the validated data + _defaulted, _dropped?,
  // _misTargeted?; split path only: _groups = [{group, chunk, fields[],
  // status, outputTokens, wallMs, retried?: "parse" | "empty" | "stream"}], one
  // row per group call — alone when no group landed) / .enrichment /
  // .synthesis / .assembly (tab drop accounting: tabsDesigned, tabsAssembled,
  // tabsDropped, droppedTabs[{id, component, dataSource, reason, detail?}] —
  // a requirement_evidence_false entry is assembly's backstop record of a
  // ruled-out required component (id = the defaultTab it would have added, ""
  // when a live tab holds it; never counted in tabsDropped) — planFallback?); pipeline.timing (above).
  pipeline: { triage, reconcile, extraction, enrichment, synthesis, assembly, timing } | absent,

  // Domain-keyed extraction data
  output: Record<string, unknown> | null,

  // Enrichment — legacy top-level copy. The pipeline writes pipeline.enrichment = {quiz}
  // (the quiz only, when the plan demanded one; flashcards/scenarios were removed in pipeline-1min 1d.1)
  enrichment: Record<string, unknown> | null,

  // Synthesis (TLDR, takeaways, master summary) — written by the API relay
  // from synthesis_complete, field by field (dotted $set of filled fields
  // only). Possibly PARTIAL: the memory-done emission brings only tldr +
  // keyTakeaways; the synthesis-done superset adds masterSummary +
  // seoDescription; either can be missing on a failure path. A partial never
  // overwrites a stored masterSummary. $unset on every (re-)dispatch of the
  // row. Rules: docs/API-REFERENCE.md#synthesis_complete.
  synthesis: {
    tldr?: string,
    keyTakeaways?: string[],
    masterSummary?: string,
    seoDescription?: string
  } | absent,

  // Multi-language support
  // For non-English videos the translation phase promotes English to the
  // top-level `tabs`/`meta` and stashes the original-language artifact
  // under `sourceLanguage`. The legacy `tabs_en`/`meta_en`/`synthesis_en`
  // /`forceEnglishReason` fields were removed in dev-1-ux (replaced by the
  // single nested block below).
  language: string | null,              // ISO 639-1 code; always "en" once translation completes
  isRTL: boolean | null,               // Mirrors `language`; always false post-translation for non-English videos
  sourceLanguage: {                     // Omitted (NOT null) for English-source and sound-only videos
    code: string,                       // ISO 639-1 code of the original ("he", "ar", ...)
    name: string,                       // Native name ("עברית", "العربية")
    isRTL: boolean,                     // Original-language direction
    tabs: TabEntry[],                   // Source-language tabs (mirror of top-level shape)
    meta: VIEResponseMeta               // Source-language meta
  } | undefined,

  // v2 Assembly output (component-addressed tabs)
  assembledMeta: {
    videoId: string,
    videoTitle: string,
    creator: string,
    contentTags: string[],
    modifiers: string[],
    primaryTag: string,
    userGoal: string,
    tldr: string,
    keyTakeaways: string[],
    masterSummary: string,
    seoDescription: string,
    language: string,           // ISO 639-1 code
    isRTL: boolean              // Whether language is RTL
  } | null,

  assembledTabs: [{
    id: string,                     // e.g., "key_points"
    label: string,                  // e.g., "Key Points"
    emoji: string,                  // e.g., "💡"
    component: string,              // Maps to COMPONENT_REGISTRY on frontend
    props: Record<string, unknown>, // Pre-resolved props for the component
    crossTabLinks?: [{              // Cross-tab navigation links
      targetTab: string,
      label: string
    }]
  }] | null,

  // Share metadata (v1.4)
  shareSlug: string | null,         // nanoid 10-char URL-safe slug (unique when set)
  sharedAt: Date | null,            // When shared
  viewsCount: number,               // Default: 0
  likesCount: number,               // Default: 0
  likedIps: string[],               // Hashed IPs for dedup

  // Cross-user content-addressed dedup (dev-1-ux)
  // SHA-256(youtubeId : PIPELINE_VERSION : providersHash : v<version>).
  // Two different users submitting the same video collapse onto a single
  // row via the partial unique index. See docs/IDEMPOTENCY.md.
  // Legacy pre-backfill rows lack this field; the index is `{ $exists: true }`
  // partial so they coexist until backfilled.
  dedupKey: string | undefined,

  // Summaries never expire on any tier. The v1.4 free-tier `expiresAt` TTL
  // was removed on 2026-09-17: MongoDB's TTL monitor hard-deleted shared rows
  // with no cascade (orphaned userVideos rows + Qdrant vectors). Legacy docs
  // are cleaned by scripts/migrate-remove-video-expiry.ts; the API drops a
  // surviving TTL index at boot (mongodb.ts hasVideoExpiryTtlIndex).

  // Versions (see docs/IDEMPOTENCY.md#version-rows-bypasscache-and-eval-runs)
  version: number,                  // shared numbering across user and eval rows
  isLatest: boolean,                // the SERVED version: the newest COMPLETED user-pool row.
                                    // A bypassCache re-run is inserted isLatest:false and promoted
                                    // only on its `completed` status (/internal/status →
                                    // promoteCompletedVersion); a failed re-run hands the requester's
                                    // library entry back to the served row (restoreServedVersion).
  evalRun: true | absent,           // D25: produced by the eval user (users.isEvalUser). Set once at
                                    // insert. Never isLatest, never version 1 (the shared dedupKey
                                    // row), hidden from other users' version lists, pruned only
                                    // among eval rows; the run writes no Redis response copy and no
                                    // Qdrant points.
  forceRefresh: true | absent,      // bypassCache: summarizer skips its Redis response cache; $unset on save
  coldMedia: true | absent,         // POST /videos `cold` (admin/eval only): the run also skips the
                                    // S3 transcript + scene-frame caches
  degraded: true | absent,          // top-level mirror of meta.degraded (admin run badge): a failed
                                    // extraction group, a dropped chunked batch, or critical coverage
  pipelineVersion: string | absent, // PIPELINE_VERSION stamp of the run that saved the row; a stale
                                    // stamp re-runs on the user's next submission (and the worker
                                    // skips a queued job only when the row is completed by the
                                    // CURRENT version)

  // Cache metadata
  processedAt: Date | null,
  processingTimeMs: number | null,  // How long it took

  // Cost tracking
  tokenUsage: {
    input: number,
    output: number,
    cost: number                    // USD
  } | null,
  totalTokens: number,              // Sum of input + output tokens (v1.4)

  // S3 media storage
  rawTranscriptRef: string | null,  // S3 key "videos/{youtubeId}/transcript.json". Only set by
                                    // scripts/backfill-transcripts.py / migrate-s3-keys.py: the
                                    // assembly-phase write-back is a no-op (string _id filter,
                                    // see summarizer-workflow.md §Assembly). Prefer the deterministic key.

  // Generation metadata (for regeneration)
  generation: {
    model: string,                  // LLM model used
    promptVersion: string,          // Prompt version (e.g., "v1.0")
    generatedAt: string             // ISO 8601 timestamp
  } | null,

  createdAt: Date,
  updatedAt: Date
}
```

> **`transcriptMeta` caveats.** Written by `pipeline_orchestration._record_transcript_outcome` via the dedicated `$set`-only `set_transcript_meta` (never through `save_structured_result`, which `$unset`s `forceRefresh`). Cleared (`$unset`) at the `processing` transition of every run and when the Redis fast path completes a row — so a completed row with **no `transcriptMeta` and no `pipelineVersion` was served from the Redis response cache**. `attempted` lists only layers that actually ran and failed: a gated layer (Whisper disabled, over the duration cap, no Gemini key) is not an attempt, and the negative-cache skip of youtube-transcript-api is reported by `captionApiSkipped` instead. `origin` is `null` when the S3 blob's recorded source had already decayed to `"s3"` (regens before the assembly re-store skip). Redis payloads, frontend responses, and public share responses never carry the block. Old rows are not migrated — flush with `scripts/wipe-data.sh` (dry run by default; `--yes` deletes the Mongo video collections, Redis, **and** every `videos/<id>/transcript.json` in the real S3 bucket — irreversible. Pass `--keep-s3` to keep the raw transcripts, in which case reprocessed rows report `source: "s3"`).
>
> ```js
> // which layer wins / how often does paid ASR run
> db.videoSummaryCache.aggregate([
>   { $match: { transcriptMeta: { $exists: true } } },
>   { $group: { _id: "$transcriptMeta.source", n: { $sum: 1 } } },
> ])
> ```

**Indexes:**
```javascript
{ youtubeId: 1, isLatest: 1 }
{ youtubeId: 1, version: -1 }
{ status: 1 }
{ outputType: 1 }                   // v1.4 — filter by output type
{ shareSlug: 1 }                    // v1.4 — unique sparse (only indexed when non-null)
{ dedupKey: 1 }                     // dev-1-ux — unique partial { $exists: true }; powers upsertCacheByDedupKey
```

---

## systemExpansionCache

One entry per chapter/concept expansion. Shared across all users.

```javascript
{
  _id: ObjectId,

  // Target reference
  videoSummaryId: ObjectId,
  targetType: "chapter" | "concept",
  targetId: string,               // UUID (or blockId for content blocks)

  // Context (cached from source)
  context: {
    videoTitle: string,
    youtubeId: string,
    // For chapters:
    timestamp?: string,
    title?: string,
    summary?: string,
    bullets?: string[],
    // For concepts:
    name?: string,
    definition?: string
  },
  
  // Generated content
  content: string,                // Markdown
  
  // Metadata
  status: "pending" | "processing" | "completed" | "failed",
  version: number,
  model: string,
  generatedAt: Date | null,
  createdAt: Date
}
```

**Indexes:**
```javascript
{ videoSummaryId: 1, targetType: 1, targetId: 1 }  // unique
{ status: 1 }
```

---

# User Data Collections

## users

```javascript
{
  _id: ObjectId,
  
  email: string,                  // unique
  passwordHash: string,           // bcrypt
  name: string,

  // Tier (v1.4) — free, pro, team
  tier: "free" | "pro" | "team",  // Default: "free"

  // D25 eval account. DB-only: set by hand (mongosh), never by an API route or
  // request input; only a literal `true` counts. Every submission of this user
  // becomes its own `evalRun` version row (see videoSummaryCache), and it may
  // submit `cold` benchmark runs.
  isEvalUser: true | absent,

  // Activity tracking
  lastLoginAt: Date | null,

  // Preferences
  preferences: {
    defaultFolder: ObjectId | null,
    theme: "light" | "dark" | "system"
  },

  // Usage limits (for rate limiting)
  usage: {
    videosThisMonth: number,
    videosResetAt: Date           // First of month
  },

  createdAt: Date,
  updatedAt: Date
}
```

**Indexes:**
```javascript
{ email: 1 }  // unique
{ tier: 1 }   // v1.4 — filter/aggregate by tier
```

---

## folders

Materialized path pattern for hierarchy.

```javascript
{
  _id: ObjectId,
  userId: ObjectId,
  
  name: string,

  // Hierarchy
  parentId: ObjectId | null,          // null = root
  path: string,                       // "/AI Learning/LLMs"
  level: number,                      // 1 = root
  
  // Display
  color: string | null,
  icon: string | null,
  order: number,
  
  createdAt: Date,
  updatedAt: Date
}
```

**Indexes:**
```javascript
{ userId: 1, path: 1 }
{ userId: 1, parentId: 1 }
```

---

## userVideos

User's video library. References shared cache.

```javascript
{
  _id: ObjectId,
  userId: ObjectId,
  
  // Reference to cache
  videoSummaryId: ObjectId,
  
  // Denormalized for display (copied from cache)
  youtubeId: string,
  title: string,
  channel: string | null,
  duration: number | null,
  thumbnailUrl: string | null,
  status: string,                 // Synced from cache
  
  // User-specific
  folderId: ObjectId | null,
  notes: string | null,
  isFavorite: boolean,            // Quick access

  // Playlist context (optional - set when imported via playlist)
  playlistInfo: {
    playlistId: string,           // YouTube playlist ID
    playlistTitle: string,        // Playlist title at import time
    position: number,             // 0-indexed position in playlist
    totalVideos: number           // Total videos at import time
  } | null,

  // Timestamps
  addedAt: Date,                  // When user added this video
  lastViewedAt: Date | null,      // Last time user opened it
  createdAt: Date,
  updatedAt: Date
}
```

**Indexes:**
```javascript
{ userId: 1, videoSummaryId: 1 }  // unique per user
{ userId: 1, folderId: 1 }
{ userId: 1, createdAt: -1 }
```

---

## userCosts

Per-user daily LLM cost aggregate. Powers the per-user reservation gate
(`POST /videos`) and the in-app usage tile.

```javascript
{
  _id: ObjectId,
  userId: ObjectId,
  date: string,                   // UTC `YYYY-MM-DD`
  totalCostUsd: number,           // Sum of per-call LLM cost, denormalized from llm_usage
  videoCount: number,             // Owned by reservation path: +1 on reserve, -1 on refund
  creditAdjustmentUsd: number,    // Admin adjustments. Negative = credit, positive = manual charge
  updatedAt: Date
}
```

**Indexes:**
```javascript
{ userId: 1, date: 1 }  // unique — enables atomic upsert under contention
{ date: 1 }             // supports admin aggregates over a trailing window
```

**Reconciliation:** `totalCostUsd` is rewritten from `llm_usage` (Python
schema: `user_id` + `cost_usd` + `timestamp`) on every terminal video status
and by the nightly `POST /internal/reconcile-costs` cron. `videoCount` is
preserved across reconciles because it tracks reservations, not real spend.

---

## userCostAdjustments

Audit log for admin grant-credit and manual-charge actions.

```javascript
{
  _id: ObjectId,
  userId: ObjectId,
  date: string,                   // UTC `YYYY-MM-DD` — adjustments are day-scoped
  amountUsd: number,              // Signed. Negative = credit (lowers spend), positive = manual charge
  reason: string,                 // 1–500 chars
  adminId: ObjectId,              // Self-attested from request body (admin service is shared-key auth)
  createdAt: Date
}
```

**Indexes:**
```javascript
{ userId: 1, createdAt: -1 }
{ adminId: 1, createdAt: -1 }
```

**Signed-storage convention:** the canonical contract across Node + Python is
`amountUsd < 0 → credit`. The admin HTTP boundary accepts a user-friendly
"positive = grant" input and inverts the sign before storage — see
`services/admin/src/routes/users.py:230-291`.

---

# Relationships

```
┌──────────────────────────────────────────────────────────────┐
│                      SYSTEM CACHE                             │
│                                                               │
│   videoSummaryCache ──────────▶ systemExpansionCache         │
│   (one per video)              (one per chapter/concept)     │
└──────────────────────────────────────────────────────────────┘
              │
              │ references
              ▼
┌──────────────────────────────────────────────────────────────┐
│                       USER DATA                               │
│                                                               │
│   userVideos ─────────────────────────────────────────────   │
│   (library)                                                  │
│       │                                                      │
│       ▼                                                      │
│   folders                                                    │
│   (organization)                                             │
└──────────────────────────────────────────────────────────────┘
```

---

# Data Integrity

## Delete Behaviors

| Action | Result |
|--------|--------|
| User removes video from library | The user's userVideos row, its idempotency keys and (if no other folder still holds the video) the user's own agentNotes are deleted. Cache stays. |
| Admin deletes video everywhere | Summarizer purge (Qdrant, S3 `videos/<id>/`, Redis), then userVideos, shareLikes, shareViews, agentNotes, idempotencyKeys, videoSummaryCache (all versions); `videoDeletions` audit row. |
| User deletes folder | Move contents to "Unfiled" or delete with contents |
| User account deleted | Delete all user data. Caches stay (shared). |

### videoDeletions

Audit row written by `VideoCascadeService` for every **global** video delete (`DELETE /api/admin/videos/:youtubeId`). User-scope deletes write no row.

```typescript
{
  _id: ObjectId,
  youtubeId: string,
  summaryIds: string[],            // every videoSummaryCache version that existed
  scope: 'global',
  initiatedBy: 'admin' | 'script',
  adminId: string | null,          // x-admin-id header, self-attested
  reason: string | null,
  counts: {                        // rows/objects removed per store
    videoSummaryCache, userVideos, shareLikes, shareViews, agentNotes,
    idempotencyKeys, dispatchGuards, qdrantPoints, s3Objects, redisKeys: number
  },
  startedAt: Date, completedAt: Date, durationMs: number,
  warnings?: string[],             // per-step failures that did not abort
}
```

Indexes: `{ youtubeId: 1 }`, `{ completedAt: -1 }`.

Note on `agentNotes.videoId` (assistant collection): video chat stores the **summary ObjectId string**, library tools store the **youtube id**; both cascades filter on both forms.

