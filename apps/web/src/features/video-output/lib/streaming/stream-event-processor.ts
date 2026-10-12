/**
 * SSE event processor for video summarization stream.
 * Maps incoming events to StreamState updates.
 */

import type { Dispatch, SetStateAction } from "react";
import {
  validateMetadataEvent,
  validateSynthesisComplete,
  validateDoneEvent,
  validateErrorEvent,
  validatePhaseEvent,
  validateTabReadyEvent,
} from "@/features/video-output/lib/streaming/sse-validators";
import type { SSEPhase, TabReadyFields } from "@/features/video-output/lib/streaming/sse-validators";
import { getUserFriendlyError } from "@/features/video-output/lib/streaming/stream-error-messages";
import { upsertStreamTab, withdrawUnlisted } from "@/features/video-output/lib/streaming/stream-tabs";
import type { StreamTab } from "@/features/video-output/lib/streaming/stream-tabs";
import { validateTabProps } from "@/features/video-output/lib/tab-prop-schemas";
import type { StreamState, FrameInfo, StreamPhase, StreamPhaseDetail } from "@/features/video-output/hooks/use-summary-stream";
import type { ContentTag, TabDefinition, VIEResponseMeta, EnrichmentData, QuizQuestion, Flashcard, CodeCheatSheetItem, ScenarioItem, Modifier, SynthesisResult } from "@vie/types";
import { filledSynthesisFields } from "@vie/types";

type SetState = Dispatch<SetStateAction<StreamState>>;

// ─── Type validation helpers ───

function isValidContentTag(v: unknown): v is ContentTag {
  return typeof v === "string" && v.length > 0;
}

function isValidModifier(v: unknown): v is Modifier {
  return typeof v === "object" && v !== null && "name" in v && typeof (v as Record<string, unknown>).name === "string";
}

function isValidTabDefinition(v: unknown): v is TabDefinition {
  if (typeof v !== "object" || v === null) return false;
  const obj = v as Record<string, unknown>;
  return typeof obj.id === "string" && typeof obj.label === "string";
}

function isValidTabLabel(v: unknown): v is { id: string; label: string; emoji: string } {
  if (typeof v !== "object" || v === null) return false;
  const obj = v as Record<string, unknown>;
  return typeof obj.id === "string" && typeof obj.label === "string" && typeof obj.emoji === "string";
}

// ─── Event Handlers (grouped by domain) ───

/** Raw SSE phase → UI phase (+ flavor detail). The summarizer's transcription
 *  layer emits finer-grained phases than the UI timeline shows; collapse them
 *  here so every emitted value maps to a visible stage instead of freezing
 *  the display on whatever phase came before.
 *  Typed total over SSEPhase so the compiler enforces the invariant: a phase
 *  added to VALID_SSE_PHASES without a mapping here (or vice versa) is a
 *  build error, never a silent UI freeze. */
const SSE_PHASE_MAP: Record<SSEPhase, { phase: StreamPhase; detail: StreamPhaseDetail | null }> = {
  metadata: { phase: "metadata", detail: null },
  transcript: { phase: "transcript", detail: null },
  transcript_cached: { phase: "transcript", detail: "captions-cached" },
  audio_transcription: { phase: "transcript", detail: "audio-transcription" },
  whisper_transcription: { phase: "transcript", detail: "audio-transcription" },
  metadata_fallback: { phase: "transcript", detail: "metadata-only" },
  triage: { phase: "extraction", detail: null },
  extraction: { phase: "extraction", detail: null },
  enrichment: { phase: "building", detail: null },
  synthesis: { phase: "building", detail: null },
  translation: { phase: "translation", detail: null },
};

function handlePhaseEvent(event: Record<string, unknown>, setState: SetState): void {
  const raw = validatePhaseEvent(event);
  if (!raw) return;
  const mapped = SSE_PHASE_MAP[raw];
  if (!mapped) return;
  setState((prev) => ({ ...prev, phase: mapped.phase, phaseDetail: mapped.detail }));
}

function handleMetadataEvent(event: Record<string, unknown>, setState: SetState): void {
  const metadata = validateMetadataEvent(event);
  setState((prev) => ({
    ...prev,
    phase: "metadata",
    phaseDetail: null,
    metadata,
    duration: metadata.duration ?? null,
  }));
}

/** Transcript finished — carries the authoritative duration (yt metadata can
 *  be absent for some sources). Phase advancement comes from triage_complete. */
function handleTranscriptReady(event: Record<string, unknown>, setState: SetState): void {
  const duration = typeof event.duration === "number" ? event.duration : null;
  if (duration == null) return;
  setState((prev) => ({ ...prev, duration }));
}

function handleTriageEvent(event: Record<string, unknown>, setState: SetState): void {
  const contentTags = Array.isArray(event.contentTags)
    ? event.contentTags.filter(isValidContentTag)
    : [];
  const modifiers = Array.isArray(event.modifiers)
    ? event.modifiers.filter(isValidModifier)
    : [];
  const primaryTag = (typeof event.primaryTag === "string" ? event.primaryTag : "learning") as ContentTag;
  const confidence = typeof event.confidence === "number" ? event.confidence : 0;
  const userGoal = typeof event.userGoal === "string" ? event.userGoal : "";
  const tabs = Array.isArray(event.tabs)
    ? event.tabs.filter(isValidTabDefinition)
    : [];

  setState((prev) => ({
    ...prev,
    phase: "extraction",
    phaseDetail: null,
    triage: { contentTags, modifiers, primaryTag, userGoal, tabs, confidence },
  }));
}

function handleExtractionProgress(event: Record<string, unknown>, setState: SetState): void {
  const section = typeof event.section === "string" ? event.section : "";
  const percent = typeof event.percent === "number" ? event.percent : 0;
  // Per-batch fields are emitted only by chunked extraction (Phase 3).
  // They are optional so legacy single/overflow events still parse.
  const batch = typeof event.batch === "number" ? event.batch : undefined;
  const of = typeof event.of === "number" ? event.of : undefined;
  setState((prev) => ({
    ...prev,
    phase: "extraction",
    phaseDetail: null,
    extractionProgress: { section, percent, batch, of },
  }));
}

function handleExtractionComplete(event: Record<string, unknown>, setState: SetState): void {
  const data = (typeof event.data === "object" && event.data !== null)
    ? event.data as Record<string, unknown>
    : {};
  setState((prev) => ({
    ...prev,
    domainData: { ...(prev.domainData ?? {}), ...data },
  }));
}

function handleEnrichmentComplete(event: Record<string, unknown>, setState: SetState): void {
  const enrichment: EnrichmentData = {};
  if (Array.isArray(event.quiz)) {
    enrichment.quiz = event.quiz.filter(
      (q): q is QuizQuestion => !!q && typeof q === 'object' && 'question' in q && Array.isArray((q as Record<string, unknown>).options)
    );
  }
  if (Array.isArray(event.flashcards)) {
    enrichment.flashcards = event.flashcards.filter(
      (f): f is Flashcard => !!f && typeof f === 'object' && 'front' in f && 'back' in f
    );
  }
  if (Array.isArray(event.cheatSheet)) {
    enrichment.cheatSheet = event.cheatSheet.filter(
      (c): c is CodeCheatSheetItem => !!c && typeof c === 'object' && 'title' in c
    );
  }
  if (Array.isArray(event.scenarios)) {
    enrichment.scenarios = event.scenarios.filter(
      (s): s is ScenarioItem => !!s && typeof s === 'object' && 'scenario' in s
    );
  }
  setState((prev) => ({ ...prev, enrichment }));
}

const EMPTY_SYNTHESIS: SynthesisResult = { tldr: "", keyTakeaways: [], masterSummary: "", seoDescription: "" };

// synthesis_complete arrives up to twice per run: early {tldr, keyTakeaways}
// at memory-done (drives the hero), then the full superset at synthesis-done.
// Either can be missing on a failure path, and a reconnect can replay them in
// any order — so emissions merge instead of replacing, and the superset wins:
// a partial (no masterSummary) arriving after the full one is a replay and is
// ignored. Same rule as the API relay's persistence (mergeSynthesis).
function handleSynthesisComplete(event: Record<string, unknown>, setState: SetState): void {
  const incoming = validateSynthesisComplete(event);
  setState((prev) => {
    if (prev.synthesis?.masterSummary && !incoming.masterSummary) return prev;
    const kept = prev.synthesis ? filledSynthesisFields(prev.synthesis) : {};
    return { ...prev, synthesis: { ...EMPTY_SYNTHESIS, ...kept, ...incoming } };
  });
}

function handleDoneEvent(event: Record<string, unknown>, setState: SetState): void {
  const { processingTimeMs, degraded } = validateDoneEvent(event);
  setState((prev) => ({
    ...prev,
    phase: "done",
    processingTimeMs,
    // Sticky: `complete` may have flagged the run already; done never unflags.
    degraded: prev.degraded || degraded,
    confettiCount: prev.isCached ? prev.confettiCount : prev.confettiCount + 1,
  }));
}

function handleErrorEvent(event: Record<string, unknown>, setState: SetState): void {
  const { message, code } = validateErrorEvent(event);
  const userFriendlyError = getUserFriendlyError(message, code);
  setState((prev) => ({
    ...prev,
    phase: "error",
    error: userFriendlyError,
  }));
}

function handleMetaEvent(event: Record<string, unknown>, setState: SetState): void {
  const meta: VIEResponseMeta = {
    videoId: typeof event.videoId === "string" ? event.videoId : "",
    videoTitle: typeof event.title === "string" ? event.title : "",
    creator: typeof event.creator === "string" ? event.creator : "",
    contentTags: Array.isArray(event.contentTags) ? event.contentTags.filter(isValidContentTag) : [],
    modifiers: Array.isArray(event.modifiers) ? event.modifiers.filter(isValidModifier) : [],
    primaryTag: (typeof event.primaryTag === "string" ? event.primaryTag : "learning") as ContentTag,
    userGoal: typeof event.userGoal === "string" ? event.userGoal : "",
    language: typeof event.language === "string" ? event.language : undefined,
    isRTL: typeof event.isRTL === "boolean" ? event.isRTL : undefined,
    degraded: event.degraded === true ? true : undefined,
  };
  const tabCount = typeof event.tabCount === "number" ? event.tabCount : 0;
  const tabLabels = Array.isArray(event.tabLabels)
    ? event.tabLabels.filter(isValidTabLabel)
    : [];
  // meta fires at PLAN time (triage phase), not assembly — setting phase
  // "building" here made the UI jump ahead and then regress on the first
  // extraction_progress. Leave the phase alone; triage_complete already set
  // "extraction" and the first assembled tab advances it.
  // Never touches tabs: a (re)subscribe starts from a reset state and
  // replace-by-id makes a replayed tab_ready idempotent, so a wipe here could
  // only lose tabs that already streamed.
  setState((prev) => ({ ...prev, meta, tabCount, tabLabels }));
}

/** Strip React-specific dangerous keys from SSE props to prevent injection. */
function sanitizeTabProps(raw: Record<string, unknown>): Record<string, unknown> {
  const safe = { ...raw };
  delete safe.dangerouslySetInnerHTML;
  delete safe.__html;
  return safe;
}

/** The StreamTab of one validated tab_ready, with only the fields it carries. */
function toStreamTab(fields: TabReadyFields, id: string): StreamTab {
  // Streamed tabs are validated per-event here (project-score-9 4.4); the same
  // shared gate re-runs over the merged tab list in ComposableOutput so cached
  // DB tabs converge on one contract. See validateTabProps in tab-prop-schemas.
  const validated = validateTabProps(fields.component, sanitizeTabProps(fields.props));
  const tab: StreamTab = {
    id,
    label: fields.label,
    emoji: fields.emoji,
    component: validated.component,
    props: validated.props,
  };
  if (fields.goal !== undefined) tab.goal = fields.goal;
  if (fields.crossTabLinks) tab.crossTabLinks = fields.crossTabLinks;
  if (fields.attachments) {
    tab.attachments = fields.attachments.map((a) => ({ ...a, props: sanitizeTabProps(a.props) }));
  }
  if (fields.degradedFrom !== undefined) tab.degradedFrom = fields.degradedFrom;
  if (fields.position !== undefined) tab.position = fields.position;
  if (fields.final === true) tab.final = true;
  if (fields.provisional === true) tab.provisional = true;
  return tab;
}

// Assembly emits no phase event: its first real tab is the "building" signal.
// The provisional Moments tab lands at plan-done, before extraction even starts.
const BEFORE_BUILDING: ReadonlySet<StreamPhase> = new Set(["idle", "connecting", "metadata", "transcript", "extraction"]);

function handleTabReady(event: Record<string, unknown>, setState: SetState): void {
  const fields = validateTabReadyEvent(event);
  setState((prev) => {
    // An id-less tab must not collapse into the previous id-less tab via
    // replace-by-id — synthesize a per-arrival id. The count suffix keeps two
    // id-less tabs with the same component+label from sharing a React key /
    // DOM id / selection target.
    const id = fields.id || `${fields.component || "tab"}-${fields.label}-${prev.tabs.length}`.trim();
    const tab = toStreamTab(fields, id);
    const next = upsertStreamTab(
      { tabs: prev.tabs, replacedTabIds: prev.replacedTabIds },
      tab,
      fields.replaces,
    );
    const building = !tab.provisional && BEFORE_BUILDING.has(prev.phase);
    if (next.tabs === prev.tabs && !building) return prev;
    return {
      ...prev,
      tabs: next.tabs,
      replacedTabIds: next.replacedTabIds,
      ...(building ? { phase: "building" as const, phaseDetail: null } : {}),
    };
  });
}

function parseTabIds(value: unknown): string[] | null {
  return Array.isArray(value) && value.every((id) => typeof id === "string") ? value : null;
}

function handleCompleteEvent(event: Record<string, unknown>, setState: SetState): void {
  const processingTimeMs = typeof event.processingTimeMs === "number" ? event.processingTimeMs : null;
  const degraded = event.degraded === true;
  // The complete event carries the ASSEMBLED tab count — replace the plan-time
  // count from meta so the "N of M" denominator stops promising dropped tabs.
  const tabCount = typeof event.tabCount === "number" ? event.tabCount : null;
  // tabIds = the final response's order: a streamed tab missing from it was
  // withdrawn (a minimum-3 fallback the quiz made unnecessary, a superseded
  // provisional tab that carried no `replaces`).
  const tabIds = parseTabIds(event.tabIds);
  setState((prev) => ({
    ...prev,
    processingTimeMs,
    degraded: prev.degraded || degraded,
    ...(tabCount !== null ? { tabCount } : {}),
    ...(tabIds !== null ? { finalTabIds: tabIds, tabs: withdrawUnlisted(prev.tabs, tabIds) } : {}),
  }));
}

function handleFramesEvent(event: Record<string, unknown>, setState: SetState): void {
  const rawFrames = Array.isArray(event.frames) ? event.frames : [];
  const frames: FrameInfo[] = rawFrames
    .filter((f): f is Record<string, unknown> => typeof f === "object" && f !== null)
    .map((f) => ({
    index: typeof f.index === "number" ? f.index : 0,
    timestamp: typeof f.timestamp === "number" ? f.timestamp : 0,
    url: typeof f.url === "string" ? f.url : "",
    s3Key: typeof f.s3Key === "string" ? f.s3Key : undefined,
    ocrText: typeof f.ocrText === "string" ? f.ocrText : undefined,
    textDensity: typeof f.textDensity === "number" ? f.textDensity : undefined,
  }));
  setState((prev) => ({ ...prev, frames }));
}

function handleWarningEvent(event: Record<string, unknown>, setState: SetState): void {
  const message = typeof event.message === "string"
    ? event.message
    : "Some operations completed with warnings";
  const rawTasks = event.failedTasks;
  const failedTasks = Array.isArray(rawTasks)
    ? rawTasks.filter((t): t is string => typeof t === "string")
    : [];
  const warningText = failedTasks.length > 0
    ? `${message} (failed: ${failedTasks.join(", ")})`
    : message;
  setState((prev) => ({
    ...prev,
    warnings: [...prev.warnings, warningText],
  }));
}

// ─── Event dispatch table ───

const EVENT_HANDLERS: Record<string, (event: Record<string, unknown>, setState: SetState) => void> = {
  cached: (_, setState) => setState((prev) => ({ ...prev, isCached: true })),
  phase: handlePhaseEvent,
  metadata: handleMetadataEvent,
  transcript_ready: handleTranscriptReady,
  triage_complete: handleTriageEvent,
  intent_detected: handleTriageEvent,
  extraction_progress: handleExtractionProgress,
  extraction_complete: handleExtractionComplete,
  enrichment_complete: handleEnrichmentComplete,
  synthesis_complete: handleSynthesisComplete,
  done: handleDoneEvent,
  error: handleErrorEvent,
  meta: handleMetaEvent,
  tab_ready: handleTabReady,
  complete: handleCompleteEvent,
  frames: handleFramesEvent,
  warning: handleWarningEvent,
  // Keepalive only — the summarizer emits these during long silent phases
  // (e.g. multi-minute Whisper) so the proxied SSE connection never idles out.
  // No state change; explicit so intent is clear (unknown events are ignored).
  heartbeat: () => {},
};

// ─── Main entry point ───

export function processEvent(
  event: Record<string, unknown>,
  setState: SetState,
): void {
  const eventType = typeof event.event === 'string' ? event.event : null;
  if (!eventType) {
    if (import.meta.env.DEV) console.warn('[processEvent] Missing or non-string event type', event);
    return;
  }

  const handler = EVENT_HANDLERS[eventType];
  if (handler) {
    handler(event, setState);
  }
}
