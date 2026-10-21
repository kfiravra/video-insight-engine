/**
 * Per-component Zod prop schemas for assembled tabs (project-score-9 4.4).
 *
 * Validated ONCE at the tab boundary — `handleTabReady` in
 * stream-event-processor.ts — so downstream renderers (component-registry.tsx)
 * can consume typed props without per-field coercion. Validation failure
 * remaps the tab to the `display_section` fallback and increments a
 * prod-visible telemetry counter (see telemetry.ts).
 *
 * Contract philosophy: loose but bounded. The Python assembler adds fields
 * ahead of the frontend, so every object is `.passthrough()` and undeclared
 * keys always pass. Declared fields are limited to what a renderer genuinely
 * needs (labels, list shapes) plus the invariants that would render as
 * garbage if violated (index bounds, enums, numeric ranges).
 */

import { z } from 'zod';
import { incrementTelemetryCounter } from '@/features/video-output/lib/telemetry';
import type { TabEntry } from '@vie/types';

// Python `None` serializes to `null` — optional assembler fields must accept
// both null and undefined, hence `.nullish()` throughout.
const optionalString = z.string().nullish();
const optionalNumber = z.number().nullish();

/** Any object shape — for renderers that tolerate arbitrary props. */
const looseObject = z.object({}).passthrough();

const nonNegativeInt = z.number().int().min(0);

function addBoundsIssue(ctx: z.RefinementCtx, path: (string | number)[], message: string): void {
  ctx.addIssue({ code: z.ZodIssueCode.custom, path, message });
}

// ─── Primary components ───

// `timestamp` (seconds) drives the seek chip; a non-numeric or negative value
// would render as a garbage chip (or seek before 0), so it is bounded here.
// `quote` is the speaker's verbatim line (output-quality R2/R10); `speaker` the
// holder of a podcast position and `category` a key idea's kind badge (1.1 / 1.3).
const spotItemSchema = z
  .object({
    name: z.string(),
    timestamp: z.number().min(0).nullish(),
    quote: optionalString,
    speaker: optionalString,
    category: optionalString,
  })
  .passthrough();

const spotExplorerSchema = z
  .object({
    spots: z.array(spotItemSchema),
    sections: z
      .array(z.object({ label: z.string(), spotIndices: z.array(nonNegativeInt) }).passthrough())
      .nullish(),
  })
  .passthrough()
  .superRefine((props, ctx) => {
    for (const [i, section] of (props.sections ?? []).entries()) {
      if (section.spotIndices.some((idx) => idx >= props.spots.length)) {
        addBoundsIssue(ctx, ['sections', i, 'spotIndices'], 'spotIndices point past the spots list');
      }
    }
  });

const momentTrackSchema = z
  .object({
    // `seconds` drives seeking — an item without it can't render as a moment. The
    // row renders `quote` (the speaker's line) and `question` (an interview's whole
    // question) as text, so a non-string there would render garbage.
    items: z.array(
      z
        .object({ label: z.string(), seconds: z.number(), quote: optionalString, question: optionalString })
        .passthrough(),
    ),
  })
  .passthrough();

// Comparison sides are string or number; `null` means the extractor dropped a
// cell and the row would render as an empty column — reject it (0 stays valid).
const comparisonSideValue = z.union([z.string(), z.number()]);

const comparisonVerdictSchema = z
  .object({
    badge: optionalString,
    bottomLine: optionalString,
    bestFor: z.array(z.string()).nullish(),
    notFor: z.array(z.string()).nullish(),
    score: optionalNumber,
    maxScore: optionalNumber,
    subScores: z.array(z.object({ category: z.string(), score: z.number() }).passthrough()).nullish(),
  })
  .passthrough();

const comparisonSchema = z
  .object({
    comparisons: z
      .array(
        z
          .object({
            feature: z.string(),
            thisProduct: comparisonSideValue,
            competitor: comparisonSideValue,
          })
          .passthrough(),
      )
      .nullish(),
    pros: z.array(z.string()).nullish(),
    cons: z.array(z.string()).nullish(),
    verdict: comparisonVerdictSchema.nullish(),
  })
  .passthrough();

const checklistSchema = z
  .object({ items: z.array(z.object({ label: z.string() }).passthrough()) })
  .passthrough();

const stepPlayerSchema = z
  .object({ steps: z.array(z.object({ instruction: z.string() }).passthrough()) })
  .passthrough();

const flashDeckSchema = z
  .object({ cards: z.array(z.object({ front: z.string(), back: z.string() }).passthrough()) })
  .passthrough();

const budgetSchema = z
  .object({
    total: z.number(),
    breakdown: z.array(z.object({ category: z.string(), amount: z.number() }).passthrough()).nullish(),
  })
  .passthrough();

// Overview is the hub renderer — every field is optional and it tolerates both
// the nested `{ data: {...} }` assembler shape and flat props.
const overviewSchema = looseObject;

const infoGridSchema = z
  .object({
    items: z.array(
      z
        .object({
          key: z.string(),
          // Optional — the assembler legitimately emits `""` (or omits the
          // field) for key-only "headline chips". Requiring it downgraded the
          // WHOLE tab to display_section over one value-less row; the renderer
          // already guards `item.value &&` per item. Transform (not .default —
          // that only covers undefined) so null normalizes to '' too.
          value: z
            .union([z.string(), z.number()])
            .nullish()
            .transform((v) => v ?? ''),
        })
        .passthrough(),
    ),
    sections: z
      .array(z.object({ label: z.string(), indices: z.array(nonNegativeInt) }).passthrough())
      .nullish(),
  })
  .passthrough()
  .superRefine((props, ctx) => {
    for (const [i, section] of (props.sections ?? []).entries()) {
      if (section.indices.some((idx) => idx >= props.items.length)) {
        addBoundsIssue(ctx, ['sections', i, 'indices'], 'indices point past the items list');
      }
    }
  });

const codePlaygroundSchema = z
  .object({ snippets: z.array(z.object({ code: z.string() }).passthrough()) })
  .passthrough();

// `explanation` is parsed for `(m:ss)` seek chips and `timestamp` (seconds)
// feeds the evidence slot, so both are bounded (output-quality R12).
const quizQuestionSchema = z
  .object({
    question: z.string(),
    options: z.array(z.string()).min(2),
    correctIndex: nonNegativeInt,
    explanation: optionalString,
    timestamp: optionalNumber,
  })
  .passthrough()
  .superRefine((q, ctx) => {
    if (q.correctIndex >= q.options.length) {
      addBoundsIssue(ctx, ['correctIndex'], `correctIndex ${q.correctIndex} out of bounds for ${q.options.length} options`);
    }
  });

const quizSchema = z.object({ questions: z.array(quizQuestionSchema) }).passthrough();

const packingMissionSchema = z
  .object({ items: z.array(z.object({ item: z.string() }).passthrough()) })
  .passthrough();

const workoutRoomSchema = z
  .object({
    exercises: z.array(z.object({ name: z.string() }).passthrough()),
    // Warmup/cooldown pass through raw from extraction — any object shape.
    warmup: z.array(looseObject).nullish(),
    cooldown: z.array(looseObject).nullish(),
  })
  .passthrough();

const lyricsKaraokeSchema = z
  .object({
    sections: z.array(
      z
        .object({ lines: z.array(z.object({ text: z.string() }).passthrough()) })
        .passthrough(),
    ),
  })
  .passthrough();

const videoFilmstripSchema = z
  .object({
    frames: z.array(z.object({ thumbnailUrl: z.string(), timestamp: z.number() }).passthrough()),
  })
  .passthrough();

const claimsTrackerSchema = z
  .object({
    claims: z.array(
      z
        .object({
          claim: z.string(),
          // Absent when the video attributes the claim to no one (output-quality 1.4).
          source: optionalString,
          // Mirrors ClaimStatus | LegacyClaimStatus in @vie/types — D11's three
          // words, plus the pre-1.4 words stored rows carry (the tracker maps them).
          // Absent on a claim without a timestamp: nobody could check it (p1 review R9).
          status: z
            .enum(['stated', 'supported', 'contested', 'verified', 'disputed', 'context'])
            .nullish(),
        })
        .passthrough(),
    ),
  })
  .passthrough();

const tierListSchema = z
  .object({
    items: z.array(
      z
        .object({
          item: z.string(),
          // Mirrors TierRank in @vie/types — out-of-enum tiers have no row.
          tier: z.enum(['S', 'A', 'B', 'C', 'D']).nullish(),
        })
        .passthrough(),
    ),
  })
  .passthrough();

const pitchCoordinate = z.number().min(0).max(100);

const formationDiagramSchema = z
  .object({
    positions: z.array(
      z.object({ player: z.string(), x: pitchCoordinate, y: pitchCoordinate }).passthrough(),
    ),
  })
  .passthrough();

// Connections come in two shapes: legacy plain strings and typed
// `{ to, type }` objects (concept-connections Pydantic sync, 2026-06-07).
const conceptConnectionSchema = z.union([z.string(), z.object({ to: z.string() }).passthrough()]);

const conceptCanvasSchema = z
  .object({
    concepts: z.array(
      z
        .object({ name: z.string(), connections: z.array(conceptConnectionSchema).nullish() })
        .passthrough(),
    ),
  })
  .passthrough();

const connectCanvasSchema = z
  .object({ pairs: z.array(z.object({ prompt: z.string(), match: z.string() }).passthrough()) })
  .passthrough();

// ─── Secondary (attachment-only) components ───

const statBannerSchema = z
  .object({
    stats: z.array(
      z.object({ label: z.string(), value: z.union([z.string(), z.number()]) }).passthrough(),
    ),
  })
  .passthrough();

const tipCalloutSchema = z
  .object({
    text: z.string(),
    style: z.enum(['tip', 'warning', 'note']).nullish(),
  })
  .passthrough();

const summaryHeaderSchema = z.object({ summary: z.string() }).passthrough();

const diagramCardSchema = z
  .object({
    nodes: z.array(z.object({ label: z.string() }).passthrough()),
    edges: z
      .array(z.object({ source: nonNegativeInt, target: nonNegativeInt }).passthrough())
      .nullish(),
  })
  .passthrough()
  .superRefine((props, ctx) => {
    for (const [i, edge] of (props.edges ?? []).entries()) {
      if (edge.source >= props.nodes.length || edge.target >= props.nodes.length) {
        addBoundsIssue(ctx, ['edges', i], 'edge endpoint points past the nodes list');
      }
    }
  });

// ─── Registry ───

export interface TabPropSchemaEntry {
  /** Loose-but-bounded contract for the component's assembled props. */
  schema: z.ZodTypeAny;
}

/**
 * Component name → prop schema. Covers every primary component in
 * domains.json `components`, every secondary in `componentTiers`, and the
 * `display_section` fallback. Parity is enforced by tab-prop-schemas.test.ts.
 */
export const TAB_PROP_SCHEMAS: Record<string, TabPropSchemaEntry> = {
  // Primary
  spot_explorer: { schema: spotExplorerSchema },
  moment_track: { schema: momentTrackSchema },
  comparison: { schema: comparisonSchema },
  checklist: { schema: checklistSchema },
  step_player: { schema: stepPlayerSchema },
  flash_deck: { schema: flashDeckSchema },
  budget: { schema: budgetSchema },
  overview: { schema: overviewSchema },
  info_grid: { schema: infoGridSchema },
  concept_canvas: { schema: conceptCanvasSchema },
  connect_canvas: { schema: connectCanvasSchema },
  // step_flow_canvas renders the same steps contract as step_player.
  step_flow_canvas: { schema: stepPlayerSchema },
  // comparison_radar is the radar-view alias of comparison (same props).
  comparison_radar: { schema: comparisonSchema },
  code_playground: { schema: codePlaygroundSchema },
  quiz_arena: { schema: quizSchema },
  packing_mission: { schema: packingMissionSchema },
  workout_room: { schema: workoutRoomSchema },
  lyrics_karaoke: { schema: lyricsKaraokeSchema },
  video_filmstrip: { schema: videoFilmstripSchema },
  claims_tracker: { schema: claimsTrackerSchema },
  tier_list: { schema: tierListSchema },
  formation_diagram: { schema: formationDiagramSchema },
  // Secondary (attachment-only) — shared contracts reuse the primary schema.
  stat_banner: { schema: statBannerSchema },
  tip_callout: { schema: tipCalloutSchema },
  summary_header: { schema: summaryHeaderSchema },
  diagram_card: { schema: diagramCardSchema },
  frame_strip: { schema: videoFilmstripSchema },
  quick_quiz: { schema: quizSchema },
  // Last-resort fallback renderer accepts anything object-shaped.
  display_section: { schema: looseObject },
};

/** Names with a registered schema — for parity tests and boundary checks. */
export const SCHEMA_COMPONENT_NAMES: ReadonlySet<string> = new Set(Object.keys(TAB_PROP_SCHEMAS));

// ─── Boundary validators ───

export interface TabPropsValidationResult {
  component: string;
  props: Record<string, unknown>;
  /** False when the tab was remapped to the display_section fallback. */
  valid: boolean;
}

/**
 * Validate one tab's props against its component contract. On unknown
 * component or schema failure the tab is remapped to `display_section` with
 * the raw payload preserved (`{ data: props }`) — malformed data renders as a
 * fallback, never garbage — and a prod-visible telemetry counter increments.
 *
 * Gate, not transform: valid tabs return the original props reference so
 * passthrough fields reach renderers byte-for-byte.
 */
export function validateTabProps(
  component: string,
  props: Record<string, unknown>,
): TabPropsValidationResult {
  const entry = TAB_PROP_SCHEMAS[component];
  if (!entry) {
    incrementTelemetryCounter('tab_component_unknown');
    console.warn(`[tab-validation] Unknown component "${component}" — rendering as display_section`);
    return { component: 'display_section', props: { data: props }, valid: false };
  }
  const result = entry.schema.safeParse(props);
  if (!result.success) {
    incrementTelemetryCounter('tab_props_invalid');
    incrementTelemetryCounter(`tab_props_invalid.${component}`);
    console.warn(
      `[tab-validation] Invalid props for "${component}" — rendering as display_section`,
      result.error.issues,
    );
    return { component: 'display_section', props: { data: props }, valid: false };
  }
  return { component, props, valid: true };
}

/**
 * Validate a whole assembled-tab list. This is the convergence point for the
 * non-SSE tab sources (cached/DB `assembledTabs`, `sourceLanguage.tabs`,
 * share pages) — SSE tabs were already gated per-event in `handleTabReady`,
 * so re-validating them is an idempotent pass (remapped tabs re-validate as
 * `display_section`, which accepts anything, and never double-count).
 * Valid entries are returned by reference so downstream `memo()` holds.
 */
export function validateTabEntries(tabs: TabEntry[]): TabEntry[] {
  return tabs.map((tab) => {
    const result = validateTabProps(tab.component, tab.props ?? {});
    return result.valid ? tab : { ...tab, component: result.component, props: result.props };
  });
}
