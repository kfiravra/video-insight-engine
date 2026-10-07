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
} from "@/features/video-output/lib/streaming/sse-validators";
import type { SSEPhase } from "@/features/video-output/lib/streaming/sse-validators";
import { getUserFriendlyError } from "@/features/video-output/lib/streaming/stream-error-messages";
import { validateTabProps } from "@/features/video-output/lib/tab-prop-schemas";
import type { StreamState, FrameInfo, StreamPhase, StreamPhaseDetail } from "@/features/video-output/hooks/use-summary-stream";
import type { ContentTag, TabDefinition, TabEntry, VIEResponseMeta, EnrichmentData, QuizQuestion, Flashcard, CodeCheatSheetItem, ScenarioItem, Modifier, SynthesisResult } from "@vie/types";

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

function isValidCrossTabLink(v: unknown): v is { targetTab: string; label: string } {
  if (typeof v !== "object" || v === null) return false;
  const obj = v as Record<string, unknown>;
  return typeof obj.targetTab === "string" && typeof obj.label === "string";
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

/** Only the fields that carry content — an empty string or array never counts
 *  as "set", so a failure-path emission can't blank what an earlier one filled. */
function filledSynthesisFields(synthesis: Partial<SynthesisResult>): Partial<SynthesisResult> {
  const filled: Partial<SynthesisResult> = {};
  if (synthesis.tldr) filled.tldr = synthesis.tldr;
  if (synthesis.keyTakeaways?.length) filled.keyTakeaways = synthesis.keyTakeaways;
  if (synthesis.masterSummary) filled.masterSummary = synthesis.masterSummary;
  if (synthesis.seoDescription) filled.seoDescription = synthesis.seoDescription;
  return filled;
}

// synthesis_complete arrives up to twice per run: early {tldr, keyTakeaways}
// at memory-done (drives the hero), then the full superset at synthesis-done.
// Either can be missing on a failure path, and a reconnect can replay them in
// any order — so emissions merge instead of replacing, and the superset wins:
// a partial (no masterSummary) arriving after the full one is a replay and is
// ignored. Same rule as the API relay's persistence (mergeSynthesis).
function handleSynthesisComplete(event: Record<string, unknown>, setState: SetState): void {
  const { tldr, keyTakeaways } = validateSynthesisComplete(event);
  const incoming = filledSynthesisFields({
    tldr,
    keyTakeaways,
    masterSummary: typeof event.masterSummary === "string" ? event.masterSummary : "",
    seoDescription: typeof event.seoDescription === "string" ? event.seoDescription : "",
  });
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
  // "extraction" and the real enrichment/synthesis phase events advance it.
  // Keep the tabs reset — it protects reconnects and cached replay.
  setState((prev) => ({ ...prev, meta, tabs: [], tabCount, tabLabels }));
}

/** Strip React-specific dangerous keys from SSE props to prevent injection. */
function sanitizeTabProps(raw: Record<string, unknown>): Record<string, unknown> {
  const safe = { ...raw };
  delete safe.dangerouslySetInnerHTML;
  delete safe.__html;
  return safe;
}

// Streamed tabs are validated per-event here (project-score-9 4.4); the same
// shared gate re-runs over the merged tab list in ComposableOutput so cached
// DB tabs converge on one contract. See validateTabProps in tab-prop-schemas.
function handleTabReady(event: Record<string, unknown>, setState: SetState): void {
  const rawProps = (typeof event.props === "object" && event.props !== null) ? event.props as Record<string, unknown> : {};
  const validated = validateTabProps(
    typeof event.component === "string" ? event.component : "",
    sanitizeTabProps(rawProps),
  );
  // An id-less tab must not collapse into the previous id-less tab via the
  // replace-by-id dedupe below — synthesize a per-position id and always
  // append. The position suffix keeps two id-less tabs with the same
  // component+label from sharing a React key / DOM id / selection target.
  const rawId = typeof event.id === "string" && event.id ? event.id : null;
  const label = typeof event.label === "string" ? event.label : "";
  const base: Omit<TabEntry, "id"> = {
    label,
    emoji: typeof event.emoji === "string" ? event.emoji : "",
    component: validated.component,
    props: validated.props,
    crossTabLinks: Array.isArray(event.crossTabLinks)
      ? event.crossTabLinks.filter(isValidCrossTabLink)
      : undefined,
  };
  // Index in the persisted tab order. moment_track tabs are held back by the
  // backend (they stream last, after their frame fill); splicing by position
  // keeps the streamed order equal to the DB doc's, so nothing reshuffles once
  // the doc becomes authoritative on completion.
  const position =
    typeof event.position === "number" && Number.isInteger(event.position) && event.position >= 0
      ? event.position
      : null;
  setState((prev) => {
    const id = rawId ?? `${validated.component || "tab"}-${label}-${prev.tabs.length}`.trim();
    const tab: TabEntry = { id, ...base };
    const existingIdx = rawId !== null ? prev.tabs.findIndex(t => t.id === tab.id) : -1;
    if (existingIdx >= 0) {
      const updated = [...prev.tabs];
      updated[existingIdx] = tab;
      return { ...prev, tabs: updated };
    }
    const tabs = [...prev.tabs];
    tabs.splice(position === null ? tabs.length : Math.min(position, tabs.length), 0, tab);
    return { ...prev, tabs };
  });
}

function handleCompleteEvent(event: Record<string, unknown>, setState: SetState): void {
  const processingTimeMs = typeof event.processingTimeMs === "number" ? event.processingTimeMs : null;
  const degraded = event.degraded === true;
  // The complete event carries the ASSEMBLED tab count — replace the plan-time
  // count from meta so the "N of M" denominator stops promising dropped tabs.
  const tabCount = typeof event.tabCount === "number" ? event.tabCount : null;
  setState((prev) => ({
    ...prev,
    processingTimeMs,
    degraded: prev.degraded || degraded,
    ...(tabCount !== null ? { tabCount } : {}),
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
