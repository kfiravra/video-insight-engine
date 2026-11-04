/**
 * Planner of the token migration (billing-tokens): reads the minute-era
 * billing collections and decides, per account, what
 * migrate-billing-tokens.ts writes. Reads only; `planAccount` is pure so the
 * apply step re-plans each account inside its own transaction.
 *
 * Conversion at the pack rate, 1 minute = 100/3 tokens ($5 bought 150 minutes
 * and buys 5,000 tokens), rounded per lot / bucket / grant:
 * - an open minute lot → a token lot of round(minutesRemaining × 100/3) with
 *   the same expiry (an expired one just closes); the minute lot is closed
 *   (`minutesRemaining: 0`, `convertedToLotId`) by a `convert` minute entry;
 * - a running Pro cycle → `tokensGranted` = the Pro allowance, `tokensUsed` =
 *   round(minutesUsed × 100/3) (capped at the allowance), term unchanged; a
 *   free plan or an ended cycle → an empty token bucket;
 * - a recurring grant → `tokens` = round(minutes × 100/3) (demo 300 → 10,000,
 *   eval 3,000 → 100,000);
 * - every live user without `grant:signup:<userId>` gets the signup grant;
 *   a user without an account gets one (with the grant) through vie-api;
 * - the free bucket, `caps.freeVideosPerDay` and the minute bucket fields go;
 *   `billingShadow` is dropped, and so are the minute-era lot indexes.
 * Open minute holds (a run in flight at deploy) block the run: wait for them
 * to finish (docs/DEPLOY.md "Migrate to the token ledger").
 */

import type { ClientSession, Db, ObjectId } from 'mongodb';

/** Pack rate: $5 bought 150 minutes and buys 5,000 tokens. */
export const MINUTES_TO_TOKENS = 100 / 3;
export const MIGRATION_ACTOR = 'system:migration';
export const MIGRATION_REF = 'billing-tokens';
export const MIGRATION_REASON = 'migration: minutes to tokens';

/** The values that live in @vie/types, passed in so this module needs no runtime import of it. */
export interface TokenConstants {
  proPeriodTokens: number;
  signupGrantTokens: number;
}

/** A stored document as the minute era (or a partly migrated account) left it. */
export type RawDoc = Record<string, unknown> & { _id: ObjectId };

export interface LotConversion {
  lotId: ObjectId;
  minutesRemaining: number;
  /** 0 when the lot has expired: it only closes. */
  tokens: number;
  expiresAt: Date | null;
  expired: boolean;
}

export interface PlanBucketConversion {
  /** Minutes the cycle still had (closed by a `convert` entry when > 0). */
  minutesLeft: number;
  minutesUsed: number;
  /** The token cycle: the allowance and what is used of it (0 / 0 for free or an ended cycle). */
  tokensGranted: number;
  tokensUsed: number;
}

export interface AccountConversion {
  accountId: ObjectId;
  ownerId: ObjectId;
  /** null for an account whose user is gone (a GDPR tombstone): converted, no signup grant. */
  email: string | null;
  planId: string;
  /** null when the plan bucket is already in tokens. */
  plan: PlanBucketConversion | null;
  lots: LotConversion[];
  /** Tokens already in token lots (written by vie-api since the deploy). */
  tokenLotTokens: number;
  signupGrant: boolean;
  /** Minute fields to drop: `buckets.free`, `buckets.credit.minutesAvailable`, `caps.freeVideosPerDay`. */
  legacyFields: string[];
  /** Minute holds still open: the run must wait for them. */
  openMinuteHolds: Array<{ requestId: string; minutes: number }>;
  /** Credit tokens after the migration (converted lots + token lots + the signup grant). */
  creditTokensAfter: number;
  /** Plan tokens left after the migration. */
  planTokensAfter: number;
  /** Nothing left to do for this account. */
  done: boolean;
}

export interface GrantConversion {
  grantId: ObjectId;
  accountId: ObjectId;
  minutes: number;
  tokens: number;
}

export interface TokenMigrationPlan {
  accounts: AccountConversion[];
  /** Live users without an account: vie-api creates one with the signup grant. */
  newAccounts: Array<{ userId: string; email: string }>;
  recurringGrants: GrantConversion[];
  /** Rows in `billingShadow` (dropped). */
  shadowRows: number;
  /** creditLots indexes partial on `minutesRemaining` (dropped). */
  minuteIndexes: string[];
}

export function tokensForMinutes(minutes: number): number {
  return Math.round(minutes * MINUTES_TO_TOKENS);
}

export const signupKey = (ownerId: ObjectId): string => `grant:signup:${ownerId.toHexString()}`;
export const convertKey = (accountId: ObjectId, what: string): string => `convert:${accountId.toHexString()}:${what}`;

function num(value: unknown): number | null {
  return typeof value === 'number' && Number.isFinite(value) ? value : null;
}

function record(value: unknown): Record<string, unknown> {
  return value !== null && typeof value === 'object' ? (value as Record<string, unknown>) : {};
}

function date(value: unknown): Date | null {
  return value instanceof Date ? value : null;
}

/** A hold written in minutes (before the token code, or normalized by it to a 0-token hold). */
export function isMinuteHold(hold: Record<string, unknown>): boolean {
  return 'minutes' in hold || num(hold.tokens) === null;
}

function planBucketConversion(account: RawDoc, constants: TokenConstants, now: Date): PlanBucketConversion | null {
  const bucket = record(record(account.buckets).plan);
  const minutesGranted = num(bucket.minutesGranted);
  if (minutesGranted === null || num(bucket.tokensGranted) !== null) return null;
  const minutesUsed = num(bucket.minutesUsed) ?? 0;
  const cycleEnd = date(bucket.cycleEnd);
  const running = record(account.plan).id === 'pro' && cycleEnd !== null && cycleEnd > now;
  const tokensGranted = running ? constants.proPeriodTokens : 0;
  return {
    minutesLeft: Math.max(minutesGranted - minutesUsed, 0),
    minutesUsed,
    tokensGranted,
    tokensUsed: running ? Math.min(tokensForMinutes(minutesUsed), tokensGranted) : 0,
  };
}

function lotConversion(lot: RawDoc, now: Date): LotConversion | null {
  const minutesRemaining = num(lot.minutesRemaining);
  if (minutesRemaining === null || minutesRemaining <= 0 || num(lot.tokensGranted) !== null) return null;
  const expiresAt = date(lot.expiresAt);
  const expired = expiresAt !== null && expiresAt <= now;
  return { lotId: lot._id, minutesRemaining, tokens: expired ? 0 : tokensForMinutes(minutesRemaining), expiresAt, expired };
}

/**
 * Minute fields to unset. A plan bucket the migration converts is replaced
 * whole; one vie-api already wrote in tokens may still carry the minute fields.
 */
function legacyFields(account: RawDoc, planConverted: boolean): string[] {
  const buckets = record(account.buckets);
  const fields: string[] = [];
  if ('free' in buckets) fields.push('buckets.free');
  if ('minutesAvailable' in record(buckets.credit)) fields.push('buckets.credit.minutesAvailable');
  if (!planConverted) {
    for (const field of ['minutesGranted', 'minutesUsed']) {
      if (field in record(buckets.plan)) fields.push(`buckets.plan.${field}`);
    }
  }
  if ('freeVideosPerDay' in record(account.caps)) fields.push('caps.freeVideosPerDay');
  return fields;
}

/** The account lacks a token bucket field (a minute account vie-api never wrote since the deploy). */
function needsTokenShape(account: RawDoc): boolean {
  const buckets = record(account.buckets);
  return num(record(buckets.debt).tokens) === null || num(record(buckets.credit).tokensAvailable) === null;
}

function remainingPlanTokens(account: RawDoc, plan: PlanBucketConversion | null): number {
  if (plan) return plan.tokensGranted - plan.tokensUsed;
  const bucket = record(record(account.buckets).plan);
  return Math.max((num(bucket.tokensGranted) ?? 0) - (num(bucket.tokensUsed) ?? 0), 0);
}

export interface AccountInput {
  account: RawDoc;
  /** Every lot of the account (minute and token lots). */
  lots: RawDoc[];
  /** The owner, when it is a live user. */
  user: { email: string } | null;
  /** `grant:signup:<ownerId>` is already in the ledger. */
  hasSignupGrant: boolean;
  constants: TokenConstants;
  now: Date;
}

/** What the migration does to one account (pure). */
export function planAccount(input: AccountInput): AccountConversion {
  const { account, lots, user, hasSignupGrant, constants, now } = input;
  const plan = planBucketConversion(account, constants, now);
  const conversions = lots.flatMap((lot) => lotConversion(lot, now) ?? []);
  const tokenLotTokens = lots.reduce((sum, lot) => sum + (num(lot.tokensRemaining) ?? 0), 0);
  const signupGrant = user !== null && !hasSignupGrant;
  const holds = Array.isArray(account.holds) ? (account.holds as Array<Record<string, unknown>>) : [];
  const openMinuteHolds = holds
    .filter(isMinuteHold)
    .map((hold) => ({ requestId: String(hold.requestId), minutes: num(hold.minutes) ?? 0 }));
  const legacy = legacyFields(account, plan !== null);
  const converted = conversions.reduce((sum, lot) => sum + lot.tokens, 0);
  return {
    accountId: account._id,
    ownerId: account.ownerId as ObjectId,
    email: user?.email ?? null,
    planId: String(record(account.plan).id ?? 'free'),
    plan,
    lots: conversions,
    tokenLotTokens,
    signupGrant,
    legacyFields: legacy,
    openMinuteHolds,
    creditTokensAfter: tokenLotTokens + converted + (signupGrant ? constants.signupGrantTokens : 0),
    planTokensAfter: remainingPlanTokens(account, plan),
    done: plan === null && conversions.length === 0 && !signupGrant && legacy.length === 0 && !needsTokenShape(account),
  };
}

/** The minute-era creditLots indexes (partial on `minutesRemaining`). */
export async function minuteIndexes(db: Db): Promise<string[]> {
  const exists = await db.listCollections({ name: 'creditLots' }, { nameOnly: true }).hasNext();
  if (!exists) return [];
  const indexes = await db.collection('creditLots').indexes();
  return indexes
    .filter((index) => JSON.stringify(index.partialFilterExpression ?? {}).includes('minutesRemaining'))
    .map((index) => index.name ?? '')
    .filter((name) => name.length > 0);
}

async function shadowRows(db: Db): Promise<number> {
  const exists = await db.listCollections({ name: 'billingShadow' }, { nameOnly: true }).hasNext();
  return exists ? db.collection('billingShadow').countDocuments({}) : 0;
}

/** Read the account's lots, owner and signup key, and plan it. */
export async function readAccountInput(
  db: Db,
  account: RawDoc,
  constants: TokenConstants,
  now: Date,
  session?: ClientSession,
): Promise<AccountInput> {
  const ownerId = account.ownerId as ObjectId;
  const lots = await db.collection<RawDoc>('creditLots').find({ accountId: account._id }, { session }).toArray();
  const owner = await db
    .collection<RawDoc>('users')
    .findOne({ _id: ownerId, deletedAt: null }, { session, projection: { email: 1 } });
  const signup = await db.collection('ledgerEntries').findOne({ idempotencyKey: signupKey(ownerId) }, { session });
  const user = owner && typeof owner.email === 'string' ? { email: owner.email } : null;
  return { account, lots, user, hasSignupGrant: signup !== null, constants, now };
}

/** The whole migration, decided from what is in the database now. Reads only. */
export async function buildTokenPlan(db: Db, constants: TokenConstants, now: Date): Promise<TokenMigrationPlan> {
  const accounts = await db.collection<RawDoc>('billingAccounts').find({ ownerType: 'user' }).sort({ _id: 1 }).toArray();
  const conversions: AccountConversion[] = [];
  for (const account of accounts) {
    conversions.push(planAccount(await readAccountInput(db, account, constants, now)));
  }
  const owners = new Set(accounts.map((account) => (account.ownerId as ObjectId).toHexString()));
  const users = await db
    .collection<RawDoc>('users')
    .find({ deletedAt: null }, { projection: { email: 1 } })
    .sort({ _id: 1 })
    .toArray();
  const newAccounts = users
    .filter((user) => !owners.has(user._id.toHexString()))
    .map((user) => ({ userId: user._id.toHexString(), email: String(user.email ?? '') }));
  const grants = await db
    .collection<RawDoc>('recurringGrants')
    .find({ tokens: { $exists: false }, minutes: { $type: 'number' } })
    .sort({ _id: 1 })
    .toArray();
  const recurringGrants = grants.map((grant) => {
    const minutes = num(grant.minutes) ?? 0;
    return { grantId: grant._id, accountId: grant.accountId as ObjectId, minutes, tokens: tokensForMinutes(minutes) };
  });
  return {
    accounts: conversions,
    newAccounts,
    recurringGrants,
    shadowRows: await shadowRows(db),
    minuteIndexes: await minuteIndexes(db),
  };
}
