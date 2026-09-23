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

The triage pipeline determines content tags from video metadata and transcript manifest. Tags drive schema selection, tab layout, and enrichment.

### 14 Primary Content Tags + 2 Modifiers

Enrichment prompts come from the `domains.json` `enrichment` map (every domain has one; `enrich_recall.txt` domains get flashcards only — no quiz/scenarios).

| ContentTag | Domain Schema | Enrichment prompt |
|------------|---------------|-------------------|
| `learning` | `schemas/learning.txt` | `enrich_study.txt` |
| `tech` | `schemas/tech.txt` | `enrich_tech.txt` |
| `fitness` | `schemas/fitness.txt` | `enrich_fitness.txt` |
| `food` | `schemas/food.txt` | `enrich_food.txt` |
| `music` | `schemas/music.txt` | `enrich_music.txt` |
| `travel` | `schemas/travel.txt` | `enrich_travel.txt` |
| `review` | `schemas/review.txt` | `enrich_review.txt` |
| `project` | `schemas/project.txt` | `enrich_project.txt` |
| `language` | `schemas/language.txt` | `enrich_language.txt` |
| `science` | `schemas/science.txt` | `enrich_science.txt` |
| `gaming` | `schemas/gaming.txt` | `enrich_recall.txt` (flashcards only) |
| `news` | `schemas/news.txt` | default (`enrich_study.txt`) |
| `podcast` | `schemas/podcast.txt` | `enrich_recall.txt` (flashcards only) |
| `sport` | `schemas/sport.txt` | default (`enrich_study.txt`) |
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
- `SSETabReadyEvent.position?: number` — index in the persisted tab order (client splices, never appends).

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
  categoryConfidence?: number; // Detection confidence
}
```

Category serves as a fallback when triage confidence < 0.6. The triage LLM now determines content tags directly from manifest + metadata.

---

# System Cache Collections

## videoSummaryCache

One entry per YouTube video. Shared across all users.

```javascript
{
  _id: ObjectId,

  // YouTube identification
  youtubeId: string,              // "dQw4w9WgXcQ" - UNIQUE
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
  // (rawTranscriptRef is NOT set for pipeline rows — see its comment below).
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
    captionLang: string | null,                          // matched caption key ("ar-SA", "en-en"); differs from the video language on auto-translation
    captionFetchError: "http_429" | "http_403" | "http_<n>" | "request" | "parse" | "empty" | null,  // "parse" = json3 body unparseable → captions discarded, chain continues
    captionApiSkipped: boolean,                          // youtube-transcript-api skipped by the Redis caption-429 marker
    attempted: string[],                                 // layers that RAN and FAILED before `source`, in order: s3 | ytdlp | api | proxy | whisper | gemini
    segments: number | null,
    chars: number | null,
    fetchWallMs: number | null,                          // wall time of the fetch chain inside the transcript phase (frames run in parallel); set on failed rows too
    errorCode: string | null                             // failed rows: TranscriptError code (NO_TRANSCRIPT, RATE_LIMITED, …), UNKNOWN_ERROR (unexpected exception) or CANCELLED (producer torn down mid-fetch)
  } | absent,

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

  // Domain-keyed extraction data
  output: Record<string, unknown> | null,

  // Enrichment (quiz, flashcards, scenarios)
  enrichment: Record<string, unknown> | null,

  // Synthesis (TLDR, takeaways, master summary)
  synthesis: {
    tldr: string,
    keyTakeaways: string[],
    masterSummary: string,
    seoDescription: string
  } | null,

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

  // Cache metadata
  version: number,
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
                                    // see summarizer-workflow.md Phase 7). Prefer the deterministic key.

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

> **`transcriptMeta` caveats.** Written by `pipeline_runner._record_transcript_outcome` via the dedicated `$set`-only `set_transcript_meta` (never through `save_structured_result`, which `$unset`s `forceRefresh`). Cleared (`$unset`) at the `processing` transition of every run and when the Redis fast path completes a row — so a completed row with **no `transcriptMeta` and no `pipelineVersion` was served from the Redis response cache**. `attempted` lists only layers that actually ran and failed: a gated layer (Whisper disabled, over the duration cap, no Gemini key) is not an attempt, and the negative-cache skip of youtube-transcript-api is reported by `captionApiSkipped` instead. `origin` is `null` when the S3 blob's recorded source had already decayed to `"s3"` (regens before the assembly re-store skip). Redis payloads, frontend responses, and public share responses never carry the block. Old rows are not migrated — flush with `scripts/wipe-data.sh` (dry run by default; `--yes` deletes the Mongo video collections, Redis, **and** every `videos/<id>/transcript.json` in the real S3 bucket — irreversible. Pass `--keep-s3` to keep the raw transcripts, in which case reprocessed rows report `source: "s3"`).
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

