import { ApiError } from "@/api/client";

// The billing refusals vie-api puts on the error envelope (see
// api/src/services/billing/billing-errors.ts). The extra fields ride in
// `ApiError.details` (the whole JSON body), and they are read defensively:
// a missing or mistyped field becomes null and the copy falls back to its
// field-less sentence rather than printing "undefined".

export type BillingNotice =
  /** 402: `needed` is a video's estimate (1 for a chat turn); `available` is the balance, negative in debt. */
  | { kind: "insufficient"; needed: number | null; available: number | null }
  | { kind: "too_long"; maxMinutes: number | null }
  | { kind: "blocked" }
  | { kind: "too_many_runs"; maxConcurrent: number | null }
  | { kind: "rate_limited"; retryAfterSec: number | null };

/** The refusals the tokens callout shows (402 / 422); the rest are a plain alert. */
export type TokensNotice = Extract<BillingNotice, { kind: "insufficient" | "too_long" }>;

type Details = Record<string, unknown>;

function numberField(details: Details, key: string): number | null {
  const value = details[key];
  return typeof value === "number" && Number.isFinite(value) ? value : null;
}

/**
 * The notice for a billing error code and its envelope fields, or null when the
 * code is not a billing refusal. RATE_LIMITED carries `retryAfterSec`; its
 * message text is never shown (the copy is localised here).
 */
export function classifyBillingCode(code: string | undefined | null, details: Details = {}): BillingNotice | null {
  switch (code) {
    case "INSUFFICIENT_TOKENS":
      return {
        kind: "insufficient",
        needed: numberField(details, "needed"),
        available: numberField(details, "available"),
      };
    case "VIDEO_TOO_LONG":
      return { kind: "too_long", maxMinutes: numberField(details, "maxMinutes") };
    case "ACCOUNT_BLOCKED":
      return { kind: "blocked" };
    case "TOO_MANY_RUNS":
      return { kind: "too_many_runs", maxConcurrent: numberField(details, "maxConcurrent") };
    case "RATE_LIMITED":
      return { kind: "rate_limited", retryAfterSec: numberField(details, "retryAfterSec") };
    default:
      return null;
  }
}

/** The notice for a failed request, or null when it is not a billing refusal. */
export function classifyBillingError(err: unknown): BillingNotice | null {
  if (!(err instanceof ApiError)) return null;
  return classifyBillingCode(err.code, err.details);
}

/** True for the refusals that the tokens callout shows (402 / 422). */
export function isTokensNotice(notice: BillingNotice): notice is TokensNotice {
  return notice.kind === "insufficient" || notice.kind === "too_long";
}
