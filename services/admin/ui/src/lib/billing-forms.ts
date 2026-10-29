import type {
  AdjustRequest,
  CreateRecurringGrantRequest,
  DeleteRecurringGrantRequest,
  SetCapsRequest,
  SetPlanRequest,
} from './billing-actions';
import type { BillingCaps } from './billing';

// Form values -> request bodies for the Account actions. Each builder returns
// the request or throws an Error whose message says what to fix; vie-api checks
// every range again, so these only spare the operator a round trip. The numbers
// are copies of the server's: `lib/limits-drift.test.ts` fails when one changes.

export const MAX_REASON_LENGTH = 500;
export const MAX_ADMIN_MINUTES = 100_000;
/** Minutes a Pro plan grants each month (`PLANS.pro.periodMinutes`). */
export const PRO_PERIOD_MINUTES = 600;

/** Cap ranges, as BillingService accepts them. */
export const CAP_LIMITS = {
  maxVideoMinutes: { min: 3, max: 180 },
  freeVideosPerDay: { min: 0, max: 100 },
  maxConcurrent: { min: 1, max: 1000 },
} as const;

export type NumericCap = keyof typeof CAP_LIMITS;

export function cleanReason(reason: string): string {
  const trimmed = reason.trim();
  if (!trimmed) throw new Error('Reason is required');
  if (trimmed.length > MAX_REASON_LENGTH) throw new Error(`Reason must be at most ${MAX_REASON_LENGTH} characters`);
  return trimmed;
}

function wholeNumber(raw: string, label: string): number {
  const value = Number(raw);
  if (raw.trim() === '' || !Number.isInteger(value)) throw new Error(`${label} must be a whole number`);
  return value;
}

// ─── Set plan ───

export interface PlanFormValues {
  planId: 'free' | 'pro';
  /** `YYYY-MM-DD` from the date input, or ''. */
  periodEnd: string;
  reason: string;
}

export function buildPlanRequest(values: PlanFormValues, now: Date = new Date()): SetPlanRequest {
  const reason = cleanReason(values.reason);
  if (values.planId === 'free' || !values.periodEnd) return { planId: values.planId, reason };

  // A date means "through the end of that day" in UTC.
  const periodEnd = new Date(`${values.periodEnd}T23:59:59.000Z`);
  if (Number.isNaN(periodEnd.getTime())) throw new Error('Term end is not a valid date');
  if (periodEnd <= now) throw new Error('Term end must be in the future');
  return { planId: 'pro', periodEnd: periodEnd.toISOString(), reason };
}

// ─── Caps ───

export interface CapsFormValues {
  maxVideoMinutes: string;
  freeVideosPerDay: string;
  maxConcurrent: string;
  blocked: boolean;
  reason: string;
}

/** The form's starting values: the caps the account has now. */
export function capsToForm(caps: BillingCaps | undefined): Omit<CapsFormValues, 'reason'> {
  return {
    maxVideoMinutes: caps?.maxVideoMinutes?.toString() ?? '',
    freeVideosPerDay: caps?.freeVideosPerDay?.toString() ?? '',
    maxConcurrent: caps?.maxConcurrent?.toString() ?? '',
    blocked: caps?.blocked === true,
  };
}

const CAP_LABELS: Record<NumericCap, string> = {
  maxVideoMinutes: 'Max video length',
  freeVideosPerDay: 'Free videos per day',
  maxConcurrent: 'Runs at once',
};

function numericCapChange(
  cap: NumericCap,
  raw: string,
  initial: string,
): number | null | undefined {
  if (raw.trim() === initial.trim()) return undefined; // untouched
  if (raw.trim() === '') return null; // emptied: clear the cap
  const value = wholeNumber(raw, CAP_LABELS[cap]);
  const { min, max } = CAP_LIMITS[cap];
  if (value < min || value > max) throw new Error(`${CAP_LABELS[cap]} must be between ${min} and ${max}`);
  return value;
}

/**
 * Only what changed is sent: a changed number is set, an emptied one clears the
 * cap (`null`), an untouched one is left out. Unticking "Block this account"
 * clears the block rather than storing `false`.
 */
export function buildCapsRequest(
  values: CapsFormValues,
  initial: Omit<CapsFormValues, 'reason'>,
): SetCapsRequest {
  const reason = cleanReason(values.reason);
  const request: SetCapsRequest = { reason };
  for (const cap of Object.keys(CAP_LIMITS) as NumericCap[]) {
    const change = numericCapChange(cap, values[cap], initial[cap]);
    if (change !== undefined) request[cap] = change;
  }
  if (values.blocked !== initial.blocked) request.blocked = values.blocked ? true : null;
  if (Object.keys(request).length === 1) throw new Error('Change at least one cap');
  return request;
}

// ─── Adjust ───

export interface AdjustFormValues {
  bucket: 'plan' | 'credit';
  minutes: string;
  reason: string;
}

export function buildAdjustRequest(values: AdjustFormValues): AdjustRequest {
  const minutes = wholeNumber(values.minutes, 'Minutes');
  if (minutes === 0) throw new Error('Minutes must not be 0');
  if (Math.abs(minutes) > MAX_ADMIN_MINUTES) throw new Error(`Minutes must be between -${MAX_ADMIN_MINUTES} and ${MAX_ADMIN_MINUTES}`);
  return { bucket: values.bucket, minutes, reason: cleanReason(values.reason) };
}

// ─── Recurring grants ───

export interface RecurringGrantFormValues {
  minutes: string;
  active: boolean;
  reason: string;
}

export function buildRecurringGrantRequest(values: RecurringGrantFormValues): CreateRecurringGrantRequest {
  const minutes = wholeNumber(values.minutes, 'Minutes');
  if (minutes < 1 || minutes > MAX_ADMIN_MINUTES) throw new Error(`Minutes must be between 1 and ${MAX_ADMIN_MINUTES}`);
  return { minutes, every: 'month', active: values.active, reason: cleanReason(values.reason) };
}

export function buildDeleteGrantRequest(reason: string): DeleteRecurringGrantRequest {
  return { reason: cleanReason(reason) };
}
