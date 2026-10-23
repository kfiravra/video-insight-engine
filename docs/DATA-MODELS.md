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

Enrichment is the quiz only (`enrich_quiz.txt`, pipeline-1min 1d.1): it runs for the `domains.json` `quizEnrichment.quizDomains` (learning, language, tech, science, podcast, news) on an instructional format only (`quizPolicy.formats`: lecture, tutorial — output-quality 4.6e), and only when the plan has a quiz host (`enrichment.needs_quiz`); the domain sets the quiz's flavor line, `quizPolicy` its size (below). The per-domain `enrich_*.txt` prompts, flashcards and scenarios are gone.

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
| `news` | `schemas/news.txt` | yes (when planned) |
| `podcast` | `schemas/podcast.txt` | yes (when planned) |
| `sport` | `schemas/sport.txt` | - |
| `narrative` | `schemas/narrative.txt` | Modifier only |
| `finance` | `schemas/finance.txt` | Modifier only |

### Interactive Component Types

Component props live in `packages/types/src/vie-response.ts`, which re-exports the tab item types (`vie-response-items.ts`) and the domain data interfaces (`vie-response-domains.ts`) — import paths are unchanged. The **concept canvas**
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
- A `moment_track` tab always exists when a moment source has entries and the steps are not the timeline (2.1; D6 below): without a planned one, assembly adds `key_moments` ("Key Moments") from the memory outline → YouTube chapters → description timestamps; it is stored, cached, RAG-indexed and translated like any tab. It only adds: it counts neither toward the minimum-3 fallbacks (a two-tab response still gets Key Info) nor toward the rich-moment-grid gate that skips the auto "Visual Moments" filmstrip, and the minimum-3 fallbacks no longer build a chapters moment_track of their own.

#### Output-quality props (2026-10)

Phase 0a is frontend-only; phase 0b is assembly code (`services/summarizer/src/services/pipeline/assembly/`; module map in [summarizer-workflow.md](./summarizer-workflow.md#assembly-notes-output-quality)); phase 1 is the registry, the prompts, the extraction schemas and assembly (fields [below](#output-quality-extraction-fields-2026-10)). All ship under `PIPELINE_VERSION` v10, so rows stored earlier keep the older shapes until the v11 bump ([IDEMPOTENCY.md](./IDEMPOTENCY.md#when-to-bump-pipeline_version)) and the FE reads both (`tech.topics` as bare names, the pre-1.4 claim statuses).

- `SpotItem.category?: string` (a key idea's kind as its badge, "Thesis") and `speaker?: string` (who holds a key idea; the card shows "— speaker" only when the tab's spots name 2+ speakers, `SpotExplorer.attributesSpeakers`), both in the FE `spotItemSchema`.
- `SpotItem.timestamp?: number | null` (seconds — the card's `SeekChip`) and `quote?: string | null` (the speaker's line, in italics, with `quoteEn?` beneath it — its English on a non-English video, 4.2): in `vie-response-items.ts` (re-exported by `vie-response.ts`), the FE `spotItemSchema` (`timestamp` ≥ 0) and the Pydantic `TravelSpot` (`timestamp` coerced to a non-negative int, else None). Assembly ships a spot's anchor as its `timestamp` (`normalizers._normalize_to_spot` → `derived_guard.anchor_seconds`: the first finite, non-negative `timestamp` / `seconds` / `startSeconds`, whole seconds; else none) and an extracted spot's own `quote`, trimmed, when non-empty; a promoted spot's `quote` is its info_grid row's `evidence` only when that is the source item's checked `quote` (`promotion.spot_evidence`, beside the row's `timestamp` and `category`; the item's translated flag and `quoteEn` are carried), else the evidence ships as prose after the spot's description (R10).
- Quiz questions (`quiz_arena`, `quick_quiz`): the FE schema (`tab-prop-schemas.ts` `quizQuestionSchema`) declares `explanation` (string) and `timestamp` (number), as `QuizArenaQuestion` already did; each `(m:ss)` / `(h:mm:ss)` in the explanation renders as a `SeekChip` (`lib/quiz-time-refs.ts` `splitTimeRefs`), and the question's `timestamp` as a trailing chip when the explanation cites no time itself (`QuizExplanation`). The quiz assembler ships `{question, options, correctIndex, explanation, timestamp?}` (`normalizers._normalize_quiz_question`; `timestamp` = the item's anchor, `derived_guard.anchor_seconds`). The quiz is untimed: the web registry passes `quizPolicy.timer` (`QUIZ_TIMER`, false) as `withTimer`.
- `info_grid` items are `{key, value, evidence?, emoji?, timestamp?, category?}` whatever the source shape (`info_grid_items._normalize_info_grid_item`). `timestamp` = the item's anchor (`derived_guard.anchor_seconds`: the first finite, non-negative numeric `timestamp` / `seconds` / `startSeconds`, whole seconds, 0 included — a string, NaN or ±Infinity is skipped); `evidence` = its first `example` / `source` / `context` / `pronunciation` / `note` / `notes` / `analogy` / `quote` that does not repeat the value; a `food.substitutions` row reads `original` → key, `substitute` → value, `notes` → evidence; `category` = a short `type` token as a badge (`chef_tip` → "Chef tip"; ≤ 32 chars, ≤ 3 words) — `type` is never the key, and the FE does not render the badge yet; a trailing "[timestamp ~Ns]" in any text field is cut and becomes the row's `timestamp` when the item has none (`stamp_marker`). A delimiterless bare string over 120 chars splits at its first sentence into key + value instead of dropping. Every row ships with a value (R5, `info_grid_items.require_grid_values`, the grid tabs and the folded grid attachments alike): a key-only row takes its `evidence` as the value, else — a one-sentence tip with a `category` — the category becomes the key and the text the value, else it is dropped; a grid of nothing but short names (≤ 40 chars, ≤ 5 words) keeps them as keys under `props.mode: "tag_cloud"`; a grid tab left with no row is dropped (`validation_empty_list`). Cells are cut to 120 chars only in the `table` / `tag_cloud` modes (`density._CAPPED_MODES`).
- A standalone `info_grid` never ships when a planned tab answers its question (R6): reconcile folds it into that tab at plan-done (the tab's `folds: [{id, label, emoji, goal, dataSource, brief, component: "info_grid"}]` in `pipeline.triage`), and assembly adds its rows to the host — as `spots` in a section named after the grid's label on a `spot_explorer` host (key → name, value → description, the row's timestamp and category; evidence → quote only when it is the item's checked quote, else prose after the description — `promotion.spot_evidence`), else as a bottom `info_grid` attachment. Only the rows inside the fold brief's `where` windows (±30 s) join, never one a spot host already shows (within 5 s of a card's second), under the label without its emoji. A planned `spot_explorer` over tips (`{type, text}`) or picks (`{item, category, note}`) ships cards read as their info_grid rows (`tip_spots.spot_card`), never a demoted grid. A host that does not ship gives the grid back as a tab of its own.
- `summary_header` — a top attachment — carries content, never the goal: on the answer tab the extraction's `summary`, whatever the tab's size or shape (title "In short"); on any other dense tab (≥ 10 items; a comparison counts rows + pros + cons), or a dense answer tab when the extraction has no `summary`, the tab's first item as `headline — body` ("First up"); ≤ 220 chars, emoji 🧭. None on a `flash_deck`, none when its text equals `TabEntry.goal` (whitespace collapsed, trailing `.!?` dropped, case-folded: `attachments._same_line`). The FE hides a stored header equal to the goal — rows assembled before 0b.6 carry one.
- `stat_banner` — a bottom attachment of the answer tab only: `stats: [{label, value, emoji?, meaning, timestamp}]`, one per anchored `learning.keyIdeas` item the model labelled `kind: "number"` with a `figure {value, label}` — `value` and `label` as the model gave them, `meaning` the claim, `timestamp` seconds (`stat_banner._key_idea_tile`; `attachments` re-exports the module's names). No text is mined for a figure (Kfir 2026-10-21: no version numbers, no sentence fragments). Attached with ≥ 5 tiles, at most 8, else no strip. `StatBannerStat` declares label / value / emoji and an optional `meaning` / `timestamp`; the FE renders `meaning` under the figure and `timestamp` as a seek chip (4.2).
- The answer tab is the first content tab after the overview that takes an attachment at all (`attachments.attach_response_secondaries`).
- Tab labels carry the shipped count (D7): a label that opens with a count claim — 1–3 ASCII digits, whitespace, then a word that is no measure unit — gets the count of the list the tab ships after the caps (`labels.true_count_label` over `labels.shipped_count`, in `core._post_process_tabs`; a comparison counts rows + pros + cons) and loses the number when no count is known. Any other label stays verbatim: a name ("7-Minute Workout", "2024 Review") or a measure, the number followed by a unit (`domains.json` `measureUnits` — `labels._MEASURE_UNITS`, case-insensitive: second(s), sec(s), minute(s), min(s), hour(s), hr(s), day(s), night(s), week(s), month(s), year(s), course(s), km, mile(s), kg, lb(s), pound(s), percent — "7 Days in Tokyo", "10 Minute Abs", "3 Course Meal"). The FE applies the same rule on render (`domain-meta.ts` `hasLeadingCount` / `trueCountLabel`, the same list as `MEASURE_UNITS`), so stored rows read true too — on a Latin-script word only: a label in another script (the translated source-language page, "7 ימים בטוקיו") is left as written, its number carried over from the assembled English label (pC).
- No Moments tab when the steps are the timeline (D6): a food, fitness or project response (the primary domain) whose requirements leave out `moment_track` (`effective_requirements`) and that ships a `step_player`, `step_flow_canvas` or `workout_room` tab ships no `moment_track` tab — a planned one is dropped (`droppedTabs` reason `steps_are_the_timeline`), the always-Moments tab is not added, and such a plan gets no provisional Moments tab at plan-done (`moment_fallback.steps_are_the_timeline`; the primaries and step components are `domains.json` `assemblyPolicy.stepTimelinePrimaries` / `sequentialComponents`). When the dedupe then drops the step tab as a twin, the Moments tabs come back and their drop rows go (`steps_timeline.restore_moments_without_steps`). Any other primary keeps its Moments — a podcast or lecture with a stray step tab included.
- `MomentItem`: an item that states no time is refused (never parked at 0:00), one item per `seconds` (the first wins), and a "Moment N" label is no text. Its start is a stated start > 0, else its `time` clock (`parse_timestamp_to_seconds` — a `learning.timestamps` item whose `seconds` is 0 or missing beside "2:10" seeks 2:10), else a stated 0 (the opening); a negative with no clock states no time (`moment_normalizers._moment_start`), and `time` ships as the clock of the `seconds` that ships. The label is `label` / `title` / `name`, else an interview `question` (its English `questionEn` first; whole up to 140 chars, else its first sentence when that fits, else a word cut), else the head of the description (≤ 60 chars); `description` is carried from `description` | `summary` | `instruction` | `answer` (`moment_normalizers.normalize_moment_items`, `_moment_text`); `quote?: string` (the line that carries the moment, verbatim, with `quoteEn?` and `quoteTranslated?` beside a kept one) and `question?: string` (a `podcast.qa` item's whole question, with its `questionStatus` and `questionEn?`) pass through. Chapter-shaped sources (chapters, the outline, description timestamps) are read in time order, an entry without a start or a title, or at a start already taken, skipped (`_chapters_to_moments`).
- `PodcastSegment.summary` also accepts `description` (`AliasChoices`; stored as `summary`; an empty or null `summary` never hides a filled `description` — `prefer_filled_prose`), so a segment's only prose survives validation and becomes its moment's `description`.
- Derived scalars (D13): a score, tier, claim status, rest time, duration or date ships only on an item anchored by `timestamp` / `seconds` / `startSeconds` (`assembly/derived_guard.py`) — and a *verdict* (score, tier, status, badge) also only beside a verified `quote` since 2.0 c ([below](#output-quality-hero-and-quiz-2026-10)); unanchored it is dropped (an unanchored claim ships with no `status` and no `sourceCitation`; `ClaimItem.status` is optional and the tracker shows no badge for it) and the item keeps its prose — the review verdict's `badge` (the verdict ships its `timestamp` and, only with it, the badge), `score` / `maxScore` / `subScores` (`normalizers._normalize_verdict`; a sub-score row ships only with a numeric `score`), tier-list tiers (none stated → no tier list: the tab demotes), step durations, exercise durations and rests, spot `cost` / `duration` / `rating`, the overview's scalars. A NaN or ±Infinity is never a number or an anchor (`stated_number`, `anchor_seconds`). Each comparison row keeps its `competitorName` (the web gives each competitor its own table); `rightLabel` names the competitor only when the rows name one, else "". A comparison tab planned on `review` or on a list path under it (`review.comparisons`, `review.pros`, `review.cons`, `review.specs`) ships the review's own `pros` / `cons` (a tab on `review.pros` ships them with no rows); one on a dict path (`review.verdict`) reads that dict's — none — and drops without 2 rows (`assemblers_primary._pros_cons`); never derived from `winner` (`assemblers_primary.assemble_comparison`). A review plan with both a rows tab (`review.comparisons`) and a Pros & cons tab (`review`, `review.pros`, `review.cons`) ships the rows on the first and the pros / cons on the second, whatever the order; a lone rows tab ships all three. The review's `verdict` rides one tab only — the Pros & cons tab when one is planned and the review states pros or cons, else the first comparison tab in plan order; a tab assembled outside a plan keeps it (`assemblers_primary._carries_verdict`) — and the web lifts it to the hero. The verdict carries its kept `quote` (`quoteTranslated`, `quoteEn`) for the hero. A tab drops when every list its component ships items through is empty (`component_lists.item_list_keys`): a comparison ships on rows, pros or cons.
- Unstated is absent, never a default (D13): the Pydantic `ReviewVerdict.badge`, `FitnessMeta.difficulty` / `duration`, `ProjectData.projectName` / `difficulty`, `LanguageData.level` and `ScienceData.level` are None when the video states none (an unknown value too); the assembled verdict carries `badge` only as stated (`normalizers._normalize_verdict`); `review.verdict.bestFor` / `notFor` hold only what the reviewer states, else `[]` (`schemas/review.txt`, 4.3) — the hero's Best for / Not for read them; an unattributed claim ships no `source` (`assemblers_secondary._normalize_claim`; `Claim.source` None) — never an invented "Reporter". In `vie-response.ts`, `ReviewVerdict.badge`, `ProjectData.projectName` / `difficulty` and `FitnessData.meta.difficulty` are optional.

#### Output-quality extraction fields (2026-10)

Pydantic in `services/summarizer/src/models/` (`idea_types.py`, `domain_types.py`, `memory_types.py`, `pipeline_types.py`), TS in `packages/types/src/vie-response.ts` + `vie-response-items.ts` + `vie-response-domains.ts` (`tests/test_ts_pydantic_parity.py` reads all three). Validation is lenient on the optional fields: a malformed stamp or an unknown enum word becomes None, never costing the item. Registry entries are in `domains.json` `dataSources`.

- `learning.keyIdeas` — `LearningKeyIdea` / `KeyIdeaItem`: `{claim, why, quote?, timestamp?, section?, kind?, speaker?, figure?}`; `kind` ∈ thesis | argument | announcement | number | story | ask | disagreement (`KEY_IDEA_KINDS`); `figure` = `{value, label}` (`KeyIdeaFigure`: both filled or None, a numeric value kept as text); a missing `claim` is taken from the item's `title` / `name` (`domain_validation`). Registry: spot_explorer + stat_banner, cap 20. Ships as spot cards (claim → name, why → description, kind → `category`, section → spot sections); `why` is the reason or mechanism the speaker gives for the claim, in their terms — never the claim reworded — or the literal "Stated without a reason" when they give none (4.6d); an `ask` is an explicit request or imperative the speaker puts to the audience, its `quote` the words that ask — an ask whose quote the quote pass cleared (or never had one) is badged Argument (`assembly/talk_asks.shown_kind`). A `disagreement` (4.1) is where host and guest differ or one pushes back: `speaker` = who pushes back, `why` = both sides, `quote` = the pushback; its rule is in `schemas/podcast.txt`, so only a podcast-tagged run is asked for it. On a talk (probe format `talk`) with key ideas, a `checklist` tab on `learning.takeaways` ships the kind-ask ideas whose quote survived as `{label: claim, note: “quote”, timestamp?, quote, quoteTranslated?, quoteEn?}` rows (a stamped idea keeps its seek point) (a translated quote as plain prose); fewer than the checklist minimum (3) drops the tab, never refilled (`talk_asks.ask_rows`); a run without key ideas, and any other format's Takeaways checklist, keeps its takeaways (`talk_asks.asks_data`). On a talk the split extraction asks for `learning.keyIdeas` for that tab, never takeaways (`field_requests._planned`). When the asks tab ships, Key Ideas sheds its ask cards so each ask shows once (`dedupe._settle_asks_view` on `talk_asks.is_asks_tab`: the takeaways checklist whose every row carries its quote, droppedTabs `duplicate_items` on the key-ideas tab); if that would leave Key Ideas under its minimum, the cards stay and the asks tab drops (`duplicate_tab`). Positions (a key-ideas tab) of a run with `podcast.guests` opens with them as its first section — "Guest" / "Guests" — each a spot card: name, role as `category` (the badge), description, `timestamp` = the intro second; two or more `disagreement` ideas close it as a "Disagreements" section (a lone one stays in its outline section, badged); the section rule is unchanged (no nav below two named sections). The key-ideas cap (`keyIdeasCapFactor` × §9) counts the guest cards on top (`core.key_idea_caps`), so a guest never pushes an idea out of Positions.
- `learning.timestamps[]` — `LearningTimestamp.description` is required (also read from `summary` / `detail`; a blank one fails the item, counted in `_dropped`) and carries `quote?`; TS `MomentItem`.
- `learning.concepts[].timestamp` — seconds, the second the concept is first explained (`LearningConcept`).
- `podcast.segments[].quote` — the line that carries the segment's conclusion (`PodcastSegment`).
- `podcast.guests[]` — `PodcastGuest`: `{name, role?, description?, emoji?, timestamp?}`. `timestamp` (4.1) is the second the guest is introduced. Any `spot_explorer` tab of a podcast-tagged run requests the guests (`field_requests._SIDE_READS`, registry cap 4), a single guest included. They ship as the first section of Positions, role as the badge (`normalizers._spot_extras`: `role` → `category`, `timestamp` → the card's seek chip); the interview playbook never plans a tab of its own for them.
- `podcast.qa` — `PodcastQA` / `PodcastQAItem`: `{question, questionEn?, answer, speaker?, timestamp?, quote?, questionStatus?}` (`answer` also read from `description`). `question` is the host's words copied from the transcript (one run of ≤ 25 words, or two joined by "…"); assembly checks it on its copy right after the quote pass (`assembly/qa_questions.check_questions`, the quote matcher's rules): found → `timestamp` = the second the question is said and `questionStatus: "verified"`; not found → dropped on an English source, on a non-English source the question is verbatim too since 4.2, with `questionEn` beside it; one the transcript does not say is kept as `questionStatus: "translated"` (D12); fewer than 3 left → `qa = []`, and a Q&A tab with no exchange drops before any fallback (droppedTabs reason `validation_empty_list`, detail "no question the transcript says — the outline never stands in"). Every row ships its whole `question` and its `questionStatus` (the moment row passes both through; the web reads a row with a `question` as an exchange). Registry: moment_track, cap 15; ships as moments.
- `tech.topics[]` — `TechTopic`: `{name, description, timestamp?}` (`name` also from `topic` / `label` / `title`; a bare string — rows stored before — is read as its name; TS `Array<TechTopic | string>`). Registry: spot_explorer first, then info_grid.
- `learning.claims`, `podcast.claims`, `tech.claims`, `news.claims` — one shape, `Claim` (`NewsClaim` is an alias) / `ClaimItem`: `{claim, source?, status?, sourceCitation?, timestamp?, quote?, quoteTranslated?, quoteEn?}` (a claim ships its `quote` whenever the quote pass kept it, 4.2; `moment_normalizers.quote_fields`), `status` ∈ stated | supported | contested (`CLAIM_STATUSES`; any other word → None, the claim stays). Registry: claims_tracker, `requiresEvidence: has_claims`, cap 20. Assembled `status` is one of the three — the pre-1.4 words read as `verified` → supported, `disputed` → contested, `context` → stated, and a missing, unknown or unanchored status is stated; TS `ClaimStatus | LegacyClaimStatus`, the tracker maps the legacy words.
- `review.rating.timestamp` (`ReviewRating`) and `review.verdict.timestamp` (`ReviewVerdict`) — the second the score / the verdict is given; `gaming.rankings[].timestamp` (`GamingRanking`, TS `TierListItem.timestamp`) — the second the creator places the item. Each is the anchor the derived-scalar rule above reads.
- `fitness.meta.duration` — None when the instructor states no length (`FitnessMeta`).
- `OutlineSection.establishes: str` (memory, ≤ 120 chars, `""` when absent) — what the section establishes; the provisional Moments tab's descriptions. The memory hero's `keyTakeaways` are 3 / 5 / 7 by length, each closing with its `[m:ss]` (stored as written in `synthesis.keyTakeaways` / `meta.keyTakeaways`); with the hero rewrite the takeaways keep that shape (below).
- `QuizQuestion.timestamp: int | None` — the second the answer is spoken (`pipeline.enrichment.quiz[]`; read from seconds or a clock in the text, "4:32", "[1:02:05]"; negative or non-finite → None; past the video's end + 10 s → the question is dropped). Always seconds: `assembly/quiz_stamps` reads a stamp that equals its explanation's clock minutes as that clock, so a minutes-as-seconds stamp never ships (2.0 a).
- `QuizQuestion.quote: str | None` — quiz v2's evidence, the speaker's words that settle the answer (≤ 25 words, blank → None); absent on rows stored before it and from the flag-off `enrich_quiz` call, and left out of every dump when unset (`exclude_if`), so the flag-off wire and stored shapes are what they were. Below.

#### Output-quality hero and quiz (2026-10)

Phase 2 (the output-quality task, 2026-10) is additive (built under v10, shipped in v11): every field below is optional, so older rows keep reading as before. Flow: [summarizer-workflow.md](./summarizer-workflow.md#hero-rewrite--quiz-v2-output-quality-21--21b).

- **Hero meta (2.1 / 2.2).** The hero rewrite (`HERO_REWRITE_ENABLED`) writes `VIEResponseMeta` (TS `vie-response.ts`, Pydantic `vie_response_v2.py` / `domain_types.py`; parity sample `readingMinutes` in `tests/test_ts_pydantic_parity.py`): `tldr`; `brief?: string` — 3–5 sentences, `masterSummary` carries the same text for older readers; `keyTakeaways: string[]` — **still strings**, each `"<claim> [m:ss]"` (`[h:mm:ss]` past an hour), the stamp copied from the extraction item the takeaway cites (`hero_rewrite.takeaway_line`), 3 / 5 / 7 by length; `worthItFor?: string` and `skipIf?: string` — one line each, ≤ 20 words, `""` when the validators dropped it (D18); `seoDescription`; `readingMinutes?: number` — whole minutes ≥ 1, counted by code over the shipped hero + every non-overview tab at 238 words a minute, written to `meta` only after the late group (`settle_tab_references`). Absent, never null, on rows stored before the hero rewrite and on a run whose hero call gave none (then `masterSummary` is the synthesis call's).
- **How the web reads it (2.3).** `VideoHero` shows `brief` (else `masterSummary`), turns each takeaway's closing `[m:ss]` / `[h:mm:ss]` into a ▶ seek chip, shows `worthItFor` / `skipIf` below the takeaways (on phones behind a third collapsed "Worth it?" row under Brief and Key takeaways, 4.4; on a review whose verdict is lifted, the third row is "Verdict" (`HeroVerdict`, 4.3) and the reviewer's stated Best for / Not for replace each line when non-empty), and replaces the bare duration with "Read: `readingMinutes` min · Watch: M min" when `readingMinutes` is set; a stored `meta` reaches it through `buildSynthesisFromMeta`, which copies the four optional fields only when set. Rows without them render as before ([FRONTEND.md](./FRONTEND.md)).
- **Where it lands.** `meta` = the assembled meta; `pipeline.synthesis` = `hero_meta`'s dict (the same keys without `readingMinutes`); the API relay's top-level `synthesis` gains `brief`, `worthItFor`, `skipIf`, `readingMinutes` from `synthesis_complete` (below, `filledSynthesisFields`); a cache hit's `synthesis_complete` carries the hero keys the stored `meta` has (`cached_response.resolve_synthesis`).
- **Quiz questions (2.1b).** `QuizQuestion.quote` (Pydantic `pipeline_types.QuizQuestion`, TS `QuizQuestion`) is the evidence line; the assembled `quiz_arena` / `quick_quiz` question ships `{question, options, correctIndex, explanation, timestamp?, quote?, quoteTranslated?}` (`normalizers._normalize_quiz_question`; the FE `quizQuestionSchema` and TS `QuizArenaQuestion` declare both). `timestamp` is set from where the quote is found in the transcript when it is found; the quiz prompts ask for it as written (`m:ss`) and `pipeline_types.quiz_timestamp_seconds` converts. Quiz v2 also carries up to 4 checked **spares** (`EnrichmentData.spares`, `exclude=True`: never dumped, emitted or stored): `phases/enrichment.settle_late_quiz` removes them from the run's record in the late group, and first drops any question whose keyed option restates a shipped takeaway (≥ 60 % of its content words and ≥ 3, `quiz_v2.restates_the_hero`) and refills from them, each spare's `correctIndex` moved to the dropped question's (`quiz_v2_select.in_slot_of`); fewer than 3 left → no `quiz_arena` tab. A bare `(N)` in an explanation is read as a second count only near the stamp or after "at" / "around" / "from" — never 1–2 digits, and off the stamp never a year (`quiz_stamps._bare_count_is_time`).
- **Quote check (2.0 d).** `quote_verify` checks every quote against the transcript the run read, on a copy; the stored `pipeline.extraction` keeps the model's text. On the copy: `verified` kept; `unverified` (English source, not found) → `quote: null` (a `quotes` list item leaves the list); `translated` (non-English source, not found) → kept, flagged **`quoteTranslated: true`** — carried on `SpotItem`, `MomentItem`, `NarrativeQuote` and the quiz question, rendered as prose without quote marks (D12). On a non-English run a flagged line not in Latin script is the speaker's language all the same (a line the check missed): the translation walk keeps it, and the 4.6c pass writes its `quoteEn`, which the English page shows as prose (pC review). `quoteStatus` is bookkeeping on that copy: no tab prop type in `@vie/types` declares it, and the overview drops it (`assemblers_learning._quote_scalars`).
- **`quoteEn` / `questionEn` (4.2 → 4.6c, D12).** On a non-English run the extraction copies every `quote` (and a `podcast.qa` question) verbatim. Its English is written by the translation phase from the line as it ships (`pipeline/translation_quotes.py`, one source→English pass), never by the extraction (G4-1). It goes beside a verified quote and a verified question — and a flagged quote or a translated question not in Latin script — on the English tabs only.
  - Pydantic: `quote_en` (alias `quoteEn`) on `LearningKeyIdea`, `PodcastQA` (also `question_en` / `questionEn`), `Claim`, `LearningTimestamp`, `PodcastSegment`, `PodcastQuote`, `TravelSpot` and `ReviewVerdict`. A blank value becomes None, and an unset one is left out of the dump (`exclude_if=idea_types.absent`), so an English run's extraction keeps its keys.
  - TS: `quoteEn?` on `SpotItem`, `KeyIdeaItem`, `MomentItem` (also `questionEn?`), `ClaimItem` (also `quoteTranslated?`), `ReviewVerdict`, `PodcastSegment`, `PodcastQAItem` (also `questionEn?`) and `PodcastQuote`.
  - Assembly: `quoteEn` rides only beside a kept `quote` — on key-idea / quote / spot cards (a promoted grid row's quote too, `promotion.spot_evidence`), moments and claims; `questionEn` rides beside an exchange's `question` and titles the row. A `podcast.quotes` / `narrative.quotes` item on flash_deck is a card: the line in front, "— speaker · m:ss" behind (else its context), and the line's `quote` / `quoteTranslated?` / `quoteEn?` beside them (`normalizers._quote_card`); an asks row carries the same three. The translation walk never sends these lines, nor a leaf of their card that restates one — a quote card's `front`, an asks row's “note” ([summarizer-workflow.md](./summarizer-workflow.md)).
  - Not covered: `ReviewRating`, `subScores[]` and `TierListItem` carry no English, and validation drops a `quoteEn` written there; a quiz question's evidence quote does get a `quoteEn` on a non-English run (the web shows it under the quiz evidence).
- **D13 extended (2.0 c).** A verdict ships only with its `timestamp` **and** a verified `quote`: the review `rating` (and its sub-scores' rows), the verdict `badge`, a tier-list `tier`, a claim `status` (TS/Pydantic `quote` on `ReviewRating`, `ReviewVerdict` and its `subScores[]`, `TierListItem`, `ClaimItem`; `derived_guard.QUOTED_FIELDS`, `has_quote`). Missing either, the item stays and the verdict goes. A tier list with no tier stated and quoted ships as a `spot_explorer` of its items (`item` → name, `reason` → description; ladder `tier_list` → `spot_explorer` → `info_grid` → `display_section`) with `degradedFrom: "tier_list"`, its goal cut before the first clause naming tiers, ranks or ratings (or, when its opening clause does, replaced by the registry's goal for the shipped component — `tab_references.goal_without_lost_structure`, `registry_goal`).
- **Dropped tabs (2.0 b).** `pipeline.assembly.droppedTabs` gains two reasons for a *planned* tab, both counted in `tabsDropped`: `filmstrip_shadowed_by_moments` (a `moment_track` with ≥ 8 frame-backed items makes a `video_filmstrip` redundant — the web's rule, run server-side, again after the moment fill) and `empty_props` (no content anywhere in its props). A tab assembly added itself leaves no row. A hero or goal sentence naming a dropped tab is removed (`tab_references.scrub_tab_references`) — but a hero field or a goal is never written as `""`: when every sentence names one, only the naming clauses go (a sentence with no clean cut stays); a takeaway's closing `[m:ss]` is stripped before the split and the takeaway goes whole when its claim does.

#### Component density gates (`densityGates`) and domain policy

`packages/shared/src/config/domains.json` carries a `densityGates` map
(`Record<string, { min; max; chars }>`, typed in
`packages/shared/src/config/index.ts`). It is **advisory planner guidance only** —
rendered into the plan prompt as `{density_gates}` to steer how many items the
LLM emits per component. It is **not** an enforcement layer: the summarizer's
per-assembler hard caps are independent. Edit `domains.json` to change either the
planner-selectable `components` list or these gates.

The same file also carries **enforced** policy blocks (helpers in
`services/summarizer/src/shared_config/domain_config.py` and `assembly_policy.py`; each
block's `…Note` key explains it). The summarizer and its worker read the file once per
process — restart both after editing it:

- `domainRequirements` — per-domain `required` / `max` / **`forbidden`** component lists (e.g. `quiz_arena` is forbidden for food, project, review, fitness, travel, music, gaming and sport, and capped at one tab elsewhere). Enforced at plan post-validation AND at assembly.
- `playbooks` — keyed `"<domain>:<format>"` (`gaming:unboxing`, `review:unboxing`, `learning:talk`, `learning:lecture`, `podcast:podcast`, `podcast:interview`, `review:review`, `news:explainer`): `required`/`preferred`/`forbidden`/`planGuidance`. Merge via `effective_requirements()`: forbidden = union, required = playbook overrides domain, max always from domain. `playbookAliases` maps a probe format that is another name for a playbook's format (`learning:interview` → `podcast:interview` — an interview the probe files under learning: S14, Talks at Google; `review:commentary` / `review:comparison` → `review:review`; `news:news` / `news:documentary` / `news:news_commentary` → `news:explainer`); a pair's own playbook wins (`get_playbook`). `review:review` opens on Pros & cons (comparison on `review.pros`); the verdict rides in the hero only (4.3, Kfir Q4). The interview guidance: Positions with disagreements as kind `disagreement` and the guests as its first section; Q&A; Conversation map; `claims_tracker` on `podcast.claims` only with `has_claims`. `learning:talk` builds its What they asked checklist from the key ideas of kind ask (each with its quote). `gaming:unboxing` prefers moment_track, spot_explorer, checklist (no info_grid, R6) and tiers a pull only where the creator ranks it in words — a rarity or price bracket is a reason (D13). The quiz is offered only where it may ship (D9 revised, output-quality 4.6e): `learning:lecture` keeps `quiz_arena` in `preferred` and "Test yourself last" in its guidance; `learning:talk`, `podcast:podcast`, `podcast:interview` and `news:explainer` (and the formats aliased to them) offer neither, so the planner does not propose a tab reconcile would drop (`reconcile_quiz_format`).
- `visualCriticality` — adaptive frame-pipeline tiers (`high`/`standard`/`low` with per-tier vision budgets, `highDomains`/`lowDomains`/`highTitleKeywords`, `highFormats` — a probe format that alone makes a video HIGH — and `skipFormats`, the probe formats whose long no-demo videos skip scene detection + vision behind `FRAMES_SKIP_WHEN_NO_VISUAL_DEMO`, output-quality 3.5).
- `quizPolicy` — `position: "last"`, `requiresEvidence: "is_learnable"`, `formats: ["lecture", "tutorial"]` (the tier-probe formats a quiz may ship on — D9 revised, output-quality 4.6e: another format drops a planned quiz tab at reconcile, `droppedTabs` `reconcile_quiz_format`, and makes no quiz call; no probe format rules nothing out), `timer: false` (the web registry's `QUIZ_TIMER`), `questionsByDuration` `[{maxMinutes, questions}]` = 3 / 5 / 8 for ≤ 15 / ≤ 45 / longer minutes (the first band whose `maxMinutes` the length does not exceed; `null` = unbounded), `attachmentHostsExclude`. `quizEnrichment.quizDomains` = learning, language, tech, science, podcast, news, each with a focus line.
- `momentCapsByDuration` — `[{maxMinutes, minutesPerMoment, min, max}]`: ≤ 15 min one per 3 (4–6), ≤ 45 one per 4 (8–12), ≤ 90 one per 5 (12–18), longer one per 6 (18–60) — a label-only Moments track gets the rate, a described one the band's max (the open-ended band: the rate).
- `assemblyPolicy` — `sequentialComponents` (step_player, step_flow_canvas, workout_room: ship whole, never capped or trimmed, D6's step tabs), `stepListDomains` (food, project: never a step canvas), `stepTimelinePrimaries` (food, fitness, project: D6), `conceptPromotionDomains` (learning, science), `noFilmstripDomains` (narrative, music), `sparseItemsMax` 4 / `denseItemsMin` 10 (attachment thresholds), `keyIdeasByDuration` `[{maxMinutes, count}]` 5 / 8 / 12 / 16 and `keyIdeasCapFactor` 2 (the key-ideas tab cap = factor × count).
- `measureUnits` — the words that make a label's leading number a measure (summarizer `labels.py`, web `domain-meta.ts` through `@vie/shared/config`, the output-quality tools).
- `injectedTabs` — `overview`, `keyInfo`, `framesGallery`, `moments`: label, emoji and viewer-outcome goal of the tabs assembly adds itself (every `defaultTabs` goal follows the same "After this you know / can …" rule).
- `infoGridHosts` — the components a standalone info_grid folds into, best first (comparison, comparison_radar, spot_explorer, step_player, step_flow_canvas, workout_room, checklist, claims_tracker, concept_canvas, code_playground).
- `grouping` — `maxTextGroups` 5, `groupOutputBudget` 1,500, `minGroupOutput` 400 (a slice estimated under it rides with the domain's previous one).

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
    phases: [{ name: string, startMs: number, endMs: number, wallMs: number }],  // metadata, transcript_frames (phase 2: text, transcript, transcript_audio = the audio re-run after the tier probe judged the captions unusable (2.5; that run also has a second tier_probe llmCall), plan, memory, frames, frames.* sub-steps incl. frames.tier_wait), visual_inject (name kept: the annotations render), extraction, split_extraction (EXTRACTION_PARALLEL: the group-extraction member of phase 2, which then also spans transcript_frames; extraction = plan-done → last group; quiz_call = the quiz call it started once the quiz's groups landed; hero_call = the hero rewrite it started once every text group landed, `synthesis` being then only the wait for it), assembly (moment_fill, synthesis, enrichment = the quiz, run inside it), translation
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
    degraded: boolean,              // a failed frame download (lowres / 720p; not a run-made cancel) or a
    degradedReasons: string[],      //   partial extraction — the same reasons as the row's degradedReasons
    costUsd: number,                // Σ costUsd over llmCalls + llmFailures — refused calls included (v4)
    counts: { llmCalls, llmFailures, retries, rateLimited,
              fallbacks,            // answers with fallbackUsed and no refusedModel: the cross-provider fallback + provider-side model swaps
              refusals,             // v4: calls refused — "Refusal" rows without refusedModel
              refusalFallbacks,     // v4: refused calls the previous model answered — llmCalls with refusedModel
              refusalsUnanswered,   // v4: refusals − refusalFallbacks
              cacheRewrites,        // hotfix 4.1, absent before: split-extraction writes of a prefix another call
              cacheRewriteTokens,   //   writes — a group released before its chunk's text1 first token that wrote,
                                    //   a later chunk's text1 writing block 1 (1h); and the tokens written
                                    //   (pipeline/cache_rewrites.py; refusal re-sends and sole writers excluded)
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
  // ({what, where[], expect}), folds on a tab that hosts a folded info_grid
  // ([{id, label, emoji, goal, dataSource, brief, component}], R6),
  // evidence ({has_steps, has_ingredients, … : bool}
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
  // (under_min), moved_last (quiz_policy), repointed (primary_moment_source:
  // a Moments tab on learning.timestamps → the primary's own moment source,
  // e.g. podcast.segments), folded (info_grid_alone: a planned info_grid
  // joined its host as one of its `folds`; `to` = the host's tab id).
  // Reasons: evidence_false, screen_code, required_missing, under_min,
  // quiz_policy, primary_moment_source, info_grid_alone, plus the drop
  // reasons — duplicate, over_max, quiz_domain, quiz_format, evidence_false, inactive_domain — which droppedTabs lists as
  // reconcile_<reason>;
  // pipeline.extraction (the validated data + _defaulted, _dropped?,
  // _misTargeted?; _dropped = {<field path>: {dropped, total}}, what
  // validation cut ("learning.keyPoints"); on a chunked split run the
  // per-chunk timestamp window (output-quality 0b.10) adds restamped and
  // cleared to each path it moved a stamp on — {dropped, total, restamped,
  // cleared}, its dropped items counted in dropped — and the stamp anchor
  // (output-quality 4.0) adds anchored (stamps moved to their verified
  // quote) and reordered (stamps > 300 s late past their list's order pulled
  // back or cleared), each only where it happened, an item it drops (a
  // timeline item left unstamped) counted in dropped;
  // split path only:
  // _groups = [{group, chunk, fields[],
  // status, outputTokens, wallMs, retried?: "parse" | "empty" | "stream"}], one
  // row per group call — alone when no group landed); chunked split runs
  // only (output-quality 3.1): _mergeRank = {status, ms, dropped?[{path, id,
  // head, keep}], held?[{path, id, guard}], ranked?[{path, from, to}],
  // sectioned?{items, sections}, moved?} — the merge_rank call's outcome) / .enrichment /
  // .synthesis / .assembly (tab drop accounting: tabsDesigned, tabsAssembled,
  // tabsDropped, droppedTabs[{id, component, dataSource, reason, detail?}] —
  // output-quality 0b adds reason duplicate_tab (the post-promotion dedupe;
  // detail "<n>% of its items ship on <id>"), duplicate_items (a twin that
  // shed the items another tab ships and still ships; detail "<removed> of
  // its <total> items ship elsewhere (<id>)"; never counted in tabsDropped)
  // and steps_are_the_timeline (a moment_track tab of a food / fitness /
  // project response navigated by its steps, D6; the row goes again when the
  // dedupe drops the step tab and the Moments come back); output-quality 2.0
  // adds filmstrip_shadowed_by_moments and empty_props (see "Output-quality
  // hero and quiz"); an info_grid tab
  // left without a valued row is validation_empty_list with detail "no row
  // with a value (R5)" and dataSource ""; a
  // requirement_evidence_false entry is assembly's backstop record of a
  // ruled-out required component (id = the defaultTab it would have added, ""
  // when a live tab holds it; never counted in tabsDropped) — planFallback?;
  // momentFill? {pending, filled, filmstripDroppedOnPending} — only on a run whose
  // moment fill was joined after `complete` (output-quality 3.7): the frames it was to
  // add, the frames it added, and whether a filmstrip went on the ones still to come);
  // pipeline.timing (above).
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
  // row. A hero-rewrite run (output-quality 2.2) adds brief / worthItFor /
  // skipIf with the superset and readingMinutes with the corrected one sent
  // after the late group — three emissions at most. That corrected one is
  // marked `final`: it $sets every field it carries (empties included) and
  // $unsets the hero keys it lacks, unguarded. Rules: docs/API-REFERENCE.md#synthesis_complete.
  synthesis: {
    tldr?: string,
    keyTakeaways?: string[],      // each may close with its "[m:ss]"
    masterSummary?: string,
    seoDescription?: string,
    brief?: string,
    worthItFor?: string,
    skipIf?: string,
    readingMinutes?: number
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
    brief?: string,             // hero rewrite (output-quality 2.2); masterSummary carries the same text
    worthItFor?: string,
    skipIf?: string,
    readingMinutes?: number,
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
  degraded: true | absent,          // admin run badge: meta.degraded (a failed extraction group, a
                                    // dropped chunked batch, or critical coverage), OR a failed frame
                                    // download (set at the run's end; no user-facing banner)
  degradedReasons: string[] | absent, // why, written at the run's end: "partial_extraction",
                                    // "media_download_failed"; [] = clean
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

