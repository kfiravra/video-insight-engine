/**
 * The read-only half of scripts/migrate-billing.ts: what the database holds now,
 * and from it the steps still to do. Nothing here writes, so `--dry-run` is this
 * file alone. Mounted next to migrate-billing.ts in the production runbook.
 */

import { createHash } from 'node:crypto';
import { ObjectId } from 'mongodb';
import type { Db } from 'mongodb';
import type { BillingRepository } from '../api/src/repositories/billing.repository.js';
import type { UserRepository } from '../api/src/repositories/user.repository.js';

export const ACTOR_ID = 'system:migration';
export const TERM_MONTHS = 12;
const TEAM_LOT_MINUTES = 600; // D10
const MINUTES_PER_USD = 30; // Q9: the pack rate, $5 = 150 minutes
const DEMO_MONTHLY_MINUTES = 300; // Q20
const EVAL_MONTHLY_MINUTES = 3000; // Kfir 2026-10-27, overrides Q20's 1,000
const MAX_LOT_MINUTES = 100_000; // BillingAdmin's per-amount ceiling

export interface MigrateOptions {
  dryRun: boolean;
  /** Admins that stay, lowercase. */
  adminEmails: string[];
  demoEmail: string | null;
  evalEmails: string[];
  /** UTC `YYYY-MM-DD` whose userCostAdjustments convert to a lot. */
  day: string;
  /** The instant every term and expiry is counted from. */
  now: Date;
}

export interface UserStep {
  userId: string;
  email: string;
}

export interface LotStep extends UserStep {
  minutes: number;
}

export interface RoleStep extends UserStep {
  action: 'promote' | 'demote';
  before: { legacyRole: 'admin'; roles: unknown; roleSource: unknown };
}

export interface Skipped {
  email: string;
  why: string;
}

export interface MigrationPlan {
  accounts: UserStep[];
  pro: UserStep[];
  team: UserStep[];
  teamLots: LotStep[];
  roles: RoleStep[];
  creditLots: LotStep[];
  demoGrants: LotStep[];
  evalGrants: LotStep[];
  skipped: Skipped[];
  /** Active admins left once the role steps are applied. */
  adminsAfter: number;
}

/** What planning reads through: the database and the repositories the application itself uses. */
export interface PlanDeps {
  db: Db;
  repo: BillingRepository;
  users: UserRepository;
  options: MigrateOptions;
}

// Kept here rather than imported: the runbook mounts only these two scripts into the container.
export function normalizeEmail(email: string): string {
  return email.trim().toLowerCase();
}

/** A valid ObjectId hex that depends only on the user and the step, so a rerun finds its own work. */
export function auditIdFor(userId: string, step: string): string {
  return createHash('sha256').update(`migrate-billing:v1:${userId}:${step}`).digest('hex').slice(0, 24);
}

/** `YYYY-MM-DD` of a date in UTC. */
export function utcDayKey(date: Date): string {
  return date.toISOString().slice(0, 10);
}

/**
 * Minutes a day's credit is worth after the day's spend: the net credit that the
 * spend has not eaten, at $1 = 30 minutes, rounded down. The epsilon keeps
 * 0.1 * 30 style products from losing a whole minute to float error.
 */
export function unconsumedMinutes(netCreditUsd: number, spendUsd: number): number {
  const unconsumed = Math.max(0, netCreditUsd - Math.max(0, spendUsd));
  return Math.floor(unconsumed * MINUTES_PER_USD + 1e-6);
}

// ─── Reading ────────────────────────────────────────────────────────────────

interface UserRow {
  id: string;
  email: string;
  tier: string;
  deleted: boolean;
  legacyAdmin: boolean;
  roles: unknown;
  roleSource: unknown;
}

async function loadUsers(db: Db): Promise<UserRow[]> {
  const projection = { email: 1, tier: 1, deletedAt: 1, role: 1, roles: 1, roleSource: 1 };
  const docs = await db.collection('users').find({}, { projection }).toArray();
  return docs.map((doc) => ({
    id: doc._id.toHexString(),
    email: typeof doc.email === 'string' ? doc.email : '',
    tier: typeof doc.tier === 'string' ? doc.tier : 'free',
    deleted: Boolean(doc.deletedAt),
    legacyAdmin: doc.role === 'admin',
    roles: doc.roles,
    roleSource: doc.roleSource,
  }));
}

/** The ledger already holds this step's entries (every ledger-writing admin step stamps its auditId as `source.ref`). */
async function ledgerStepDone(deps: PlanDeps, userId: string, step: string): Promise<boolean> {
  return (await deps.repo.findLedgerBySourceRef(auditIdFor(userId, step))).length > 0;
}

async function recurringStepDone(deps: PlanDeps, userId: string, step: string): Promise<boolean> {
  const account = await deps.repo.findAccount('user', userId);
  if (!account) return false;
  return (await deps.repo.findRecurringGrantByAudit(account._id, new ObjectId(auditIdFor(userId, step)))) !== null;
}

// ─── Planning ───────────────────────────────────────────────────────────────

async function planAccounts(deps: PlanDeps, users: UserRow[]): Promise<UserStep[]> {
  const missing: UserStep[] = [];
  for (const user of users.filter((candidate) => !candidate.deleted)) {
    if (!(await deps.repo.findAccount('user', user.id))) missing.push({ userId: user.id, email: user.email });
  }
  return missing;
}

type TierPlan = Pick<MigrationPlan, 'pro' | 'team' | 'teamLots' | 'skipped'>;

async function planTiers(deps: PlanDeps, users: UserRow[]): Promise<TierPlan> {
  const plan: TierPlan = { pro: [], team: [], teamLots: [], skipped: [] };
  for (const user of users.filter((candidate) => !candidate.deleted)) {
    if (user.tier !== 'pro' && user.tier !== 'team') continue;
    const step = { userId: user.id, email: user.email };
    const account = await deps.repo.findAccount('user', user.id);
    const planDone = await ledgerStepDone(deps, user.id, 'plan');
    if (!planDone && account?.plan.id === 'pro') {
      plan.skipped.push({ email: user.email, why: 'already on a Pro plan; left as it is' });
    } else if (!planDone) {
      (user.tier === 'pro' ? plan.pro : plan.team).push(step);
    }
    if (user.tier === 'team' && !(await ledgerStepDone(deps, user.id, 'team-lot'))) {
      plan.teamLots.push({ ...step, minutes: TEAM_LOT_MINUTES });
    }
  }
  return plan;
}

function planRoleStep(user: UserRow, keep: Set<string>): RoleStep | null {
  const roles: unknown[] | null = Array.isArray(user.roles) ? user.roles : null;
  // `roles: []` is a demoted user: the array wins over a leftover legacy `role`.
  if (!user.legacyAdmin || roles?.length === 0) return null;
  const before = { legacyRole: 'admin' as const, roles: user.roles, roleSource: user.roleSource };
  const step = { userId: user.id, email: user.email, before };
  if (keep.has(normalizeEmail(user.email)) && !user.deleted) {
    const done = roles?.includes('admin') === true && user.roleSource === 'env';
    return done ? null : { ...step, action: 'promote' };
  }
  return { ...step, action: 'demote' };
}

async function planRoles(deps: PlanDeps, users: UserRow[]): Promise<Pick<MigrationPlan, 'roles' | 'adminsAfter'>> {
  const keep = new Set(deps.options.adminEmails);
  const roles = users.flatMap((user) => planRoleStep(user, keep) ?? []);
  const demoted = new Set(roles.filter((step) => step.action === 'demote').map((step) => step.userId));
  const admins = await deps.users.listAdmins();
  const adminsAfter = admins.filter((admin) => !admin.deletedAt && !demoted.has(admin.id)).length;
  return { roles, adminsAfter };
}

/** Net credit per user, in dollars, of the adjustments dated `day`: a grant is stored negative, a charge positive. */
async function netCreditByUser(db: Db, day: string): Promise<Map<string, number>> {
  const rows = await db.collection('userCostAdjustments').find({ date: day }).toArray();
  const net = new Map<string, number>();
  for (const row of rows) {
    const id = row.userId.toHexString();
    net.set(id, (net.get(id) ?? 0) - Number(row.amountUsd));
  }
  return net;
}

async function planCredits(deps: PlanDeps, users: UserRow[]): Promise<Pick<MigrationPlan, 'creditLots' | 'skipped'>> {
  const { db, options } = deps;
  const byId = new Map(users.map((user) => [user.id, user]));
  const plan: Pick<MigrationPlan, 'creditLots' | 'skipped'> = { creditLots: [], skipped: [] };
  for (const [userId, netUsd] of await netCreditByUser(db, options.day)) {
    const user = byId.get(userId);
    if (!Number.isFinite(netUsd) || netUsd <= 0) continue;
    if (!user || user.deleted) {
      plan.skipped.push({ email: user?.email ?? userId, why: 'credit for a missing or soft-deleted user' });
      continue;
    }
    if (await ledgerStepDone(deps, userId, 'credit-lot')) continue;
    const spent = await db.collection('userCosts').findOne({ userId: new ObjectId(userId), date: options.day });
    const minutes = unconsumedMinutes(Math.round(netUsd * 100) / 100, Number(spent?.totalCostUsd) || 0);
    if (!(minutes >= 1 && minutes <= MAX_LOT_MINUTES)) {
      plan.skipped.push({ email: user.email, why: `credit of $${netUsd.toFixed(2)} converts to ${minutes} minutes; not granted` });
      continue;
    }
    plan.creditLots.push({ userId, email: user.email, minutes });
  }
  return plan;
}

interface RecurringSpec {
  emails: string[];
  minutes: number;
  step: string;
}

async function planRecurring(deps: PlanDeps, users: UserRow[], spec: RecurringSpec): Promise<{ grants: LotStep[]; skipped: Skipped[] }> {
  const byEmail = new Map(users.map((user) => [normalizeEmail(user.email), user]));
  const result: { grants: LotStep[]; skipped: Skipped[] } = { grants: [], skipped: [] };
  for (const email of spec.emails) {
    const user = byEmail.get(email);
    if (!user || user.deleted) {
      result.skipped.push({ email, why: `${spec.step}: no active account with this email` });
    } else if (!(await recurringStepDone(deps, user.id, spec.step))) {
      result.grants.push({ userId: user.id, email: user.email, minutes: spec.minutes });
    }
  }
  return result;
}

/** Everything the migration still has to do, from the database as it is now. Reads only. */
export async function buildPlan(deps: PlanDeps): Promise<MigrationPlan> {
  const { options } = deps;
  const users = await loadUsers(deps.db);
  const tiers = await planTiers(deps, users);
  const roles = await planRoles(deps, users);
  const credits = await planCredits(deps, users);
  const demo = await planRecurring(deps, users, {
    emails: options.demoEmail ? [options.demoEmail] : [],
    minutes: DEMO_MONTHLY_MINUTES,
    step: 'recurring-demo',
  });
  const evals = await planRecurring(deps, users, {
    emails: options.evalEmails,
    minutes: EVAL_MONTHLY_MINUTES,
    step: 'recurring-eval',
  });
  return {
    accounts: await planAccounts(deps, users),
    pro: tiers.pro,
    team: tiers.team,
    teamLots: tiers.teamLots,
    roles: roles.roles,
    adminsAfter: roles.adminsAfter,
    creditLots: credits.creditLots,
    demoGrants: demo.grants,
    evalGrants: evals.grants,
    skipped: [...tiers.skipped, ...credits.skipped, ...demo.skipped, ...evals.skipped],
  };
}
