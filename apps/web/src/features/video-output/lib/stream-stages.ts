/**
 * The streaming timeline's stages — shared by the pre-tab StreamingPlaceholder
 * (full timeline) and the in-tab progress strip (compact row).
 */

import type { StreamPhase } from "@/features/video-output/hooks/use-summary-stream";

export interface StageDef {
  id: "metadata" | "transcript" | "extraction" | "building" | "translation";
  label: string;
  /** The compact strip's label. */
  shortLabel: string;
}

const BASE_STAGES: StageDef[] = [
  { id: "metadata", label: "Read the video", shortLabel: "Read" },
  { id: "transcript", label: "Get the transcript", shortLabel: "Transcript" },
  { id: "extraction", label: "Extract the knowledge", shortLabel: "Extract" },
  { id: "building", label: "Build your tabs", shortLabel: "Build" },
];

const TRANSLATION_STAGE: StageDef = { id: "translation", label: "Translate", shortLabel: "Translate" };

/** Where each stream phase lands on the stage timeline. idle/connecting sit
 *  on the first stage — connection is sub-second and doesn't earn a row.
 *  Total over StreamPhase so a new phase can't silently fall back to stage 0:
 *  done/cancelled/error park past the last stage (everything complete). */
export const STAGE_INDEX: Record<StreamPhase, number> = {
  idle: 0,
  connecting: 0,
  metadata: 0,
  transcript: 1,
  extraction: 2,
  building: 3,
  translation: 4,
  done: 5,
  cancelled: 5,
  error: 5,
};

/** Translation is a tail stage that only exists for non-English sources —
 *  its row appears the moment the pipeline enters it instead of showing a
 *  permanently-skipped step to everyone else. */
export function stagesFor(phase: StreamPhase): StageDef[] {
  return phase === "translation" ? [...BASE_STAGES, TRANSLATION_STAGE] : BASE_STAGES;
}
