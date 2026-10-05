/** Router state that carries a URL to the Generate page for auto-submit. */
export interface SubmitUrlState {
  submitUrl: string;
}

/** URL handed over via router state (landing page, or login on its behalf), if any. */
export function readSubmitUrl(state: unknown): string | undefined {
  if (typeof state !== "object" || state === null || !("submitUrl" in state)) {
    return undefined;
  }
  return typeof state.submitUrl === "string" ? state.submitUrl : undefined;
}
