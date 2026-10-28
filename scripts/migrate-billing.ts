/**
 * One-off billing migration (billing-roles P3.8): moves a host that ran the dollar
 * gate onto the minutes ledger. Idempotent: a second run changes nothing.
 *
 * Steps, in order: an account per user; `tier: 'pro'` -> manual Pro for 12 months
 * (Q19); `tier: 'team'` -> the same Pro plan + a 600-minute lot, 12 months, reason
 * "migration: team tier" (D10); a legacy `role: 'admin'` user on --admin-emails ->
 * `roles: ['admin']`, `roleSource: 'env'`, every other legacy admin demoted (Q15);
 * the adjustments dated the migration day -> one lot per user at $1 = 30 minutes,
 * 12 months (Q9); recurring grants: --demo-email 300/month, each --eval-emails
 * account 3,000/month (Q20, Kfir 2026-10-27).
 *
 * "Unconsumed" credit (Q9) is deliberately conservative: the day's net credit (a
 * grant is a negative `amountUsd`, a charge a positive one) minus everything the
 * user spent that day (`userCosts.totalCostUsd`), as if spend ate credit first,
 * rounded down to whole minutes. Adjustments of any other day had expired.
 *
 * Writes only through vie-api's own code: BillingService (accounts, plans, lots,
 * recurring grants), UserRepository.setRoles and AuditService. The admin API fixes
 * a ledger entry's source kind to `admin`, so migration entries are told apart by
 * actor `system:migration` and a "migration: ..." reason. Every step has an auditId
 * derived from user + step, so a step that already ran is skipped. Role changes also
 * get an `adminAudit` row (`roles.migration.promote|demote`). What to do is decided
 * in migrate-billing-plan.ts, which only reads.
 *
 * Refuses (exit 1, nothing written, dry run included) when the result would leave
 * no active admin. `--run` against a production-looking URI needs --yes-prod.
 *
 * Usage (production: docs/DEPLOY.md "Migrate to the minutes ledger"; both scripts
 * are mounted into /app/api in a one-off container):
 *   npx tsx migrate-billing.ts (--dry-run | --run) [--admin-emails <list>]
 *     [--demo-email <email>] [--eval-emails <list>] [--day YYYY-MM-DD] [--yes-prod]
 *   Local: pnpm --filter @vie/api exec tsx ../scripts/migrate-billing.ts --dry-run
 * Defaults: --admin-emails = $ADMIN_EMAILS, --demo-email = $DEMO_USER_EMAIL, --day =
 * today (UTC).
 *
 * Env: MONGODB_URI (default mongodb://localhost:27017/video-insight-engine?directConnection=true).
 */

import { resolve } from 'node:path';
import { pathToFileURL } from 'node:url';
import { parseArgs } from 'node:util';
import { MongoClient } from 'mongodb';
import type { Db } from 'mongodb';
import type { FastifyBaseLogger } from 'fastify';
import type { Role } from '@vie/types';
import type { AuditService } from '../api/src/services/audit.service.js';
import type { AdminMutationContext, BillingService } from '../api/src/services/billing/billing.service.js';
import { ACTOR_ID, TERM_MONTHS, auditIdFor, buildPlan, compareKeepList, normalizeEmail, utcDayKey } from './migrate-billing-plan.js';
import type { LotStep, MigrateOptions, MigrationPlan, PlanDeps, RoleStep, Skipped, UserStep } from './migrate-billing-plan.js';

export { auditIdFor, unconsumedMinutes, utcDayKey } from './migrate-billing-plan.js';
export type { MigrateOptions, MigrationPlan } from './migrate-billing-plan.js';

const USAGE =
  'Usage: migrate-billing.ts (--dry-run | --run) [--admin-emails <email,…>] [--demo-email <email>] ' +
  '[--eval-emails <email,…>] [--day YYYY-MM-DD] [--yes-prod]';

const DEFAULT_URI = 'mongodb://localhost:27017/video-insight-engine?directConnection=true';
const LOCAL_HOSTS = new Set(['localhost', '127.0.0.1', '::1', '[::1]', 'vie-mongodb']);

/** Raised before any write when the role steps would leave no active admin. */
export class NoAdminsRemainError extends Error {
  constructor() {
    super('this would leave zero active admins (is the right email in --admin-emails?); nothing was changed.');
    this.name = 'NoAdminsRemainError';
  }
}

/** Raised before anything is read or written when Mongo is a standalone server: the ledger needs transactions. */
export class StandaloneMongoError extends Error {
  constructor() {
    super(
      'MongoDB is a standalone server (hello has no setName) and the minutes ledger needs a replica set for transactions. ' +
        'Convert it first (docs/DEPLOY.md "Convert Mongo to a replica set"); nothing was changed.',
    );
    this.name = 'StandaloneMongoError';
  }
}

/** Raised before any write when `--run` targets a production-looking URI without --yes-prod. */
export class ProdTargetError extends Error {
  constructor() {
    super('MONGODB_URI looks like production (credentials or a remote host); pass --yes-prod to run against it.');
    this.name = 'ProdTargetError';
  }
}

/** True for a URI with credentials, an SRV record, or any host that is not this machine or the dev compose. */
export function looksLikeProd(uri: string): boolean {
  const match = /^mongodb(\+srv)?:\/\/(?:([^@/]+)@)?([^/?]+)/.exec(uri.trim());
  if (!match) return true;
  const [, srv, credentials, hosts] = match;
  if (srv || credentials) return true;
  return hosts.split(',').some((host) => !LOCAL_HOSTS.has(host.replace(/:\d+$/, '').toLowerCase()));
}

// ─── Applying (every write goes through vie-api's own code) ──────────────────

interface ApplyDeps extends PlanDeps {
  billing: BillingService;
  audit: AuditService;
  addMonthsUtc: Api['addMonthsUtc'];
}

/** BillingService and AuditService only log warnings; the script reports through its own summary. */
function silentLogger(): FastifyBaseLogger {
  const noop = (): void => undefined;
  const logger = { info: noop, warn: noop, error: noop, debug: noop, trace: noop, fatal: noop, silent: noop, level: 'silent' };
  return Object.assign(logger, { child: () => logger }) as unknown as FastifyBaseLogger;
}

/**
 * vie-api's classes, loaded with import(): scripts/ has no "type": "module", so tsx
 * compiles this file as CommonJS, where a static import of the ESM sources would
 * resolve @vie/types by its `require` conditions and fail. import() keeps them ESM.
 */
async function loadApi() {
  const [billingRepo, userRepo, auditRepo, auditService, billingService, minutes] = await Promise.all([
    import('../api/src/repositories/billing.repository.js'),
    import('../api/src/repositories/user.repository.js'),
    import('../api/src/repositories/admin-audit.repository.js'),
    import('../api/src/services/audit.service.js'),
    import('../api/src/services/billing/billing.service.js'),
    import('../api/src/services/billing/minutes.js'),
  ]);
  return { ...billingRepo, ...userRepo, ...auditRepo, ...auditService, ...billingService, addMonthsUtc: minutes.addMonthsUtc };
}

type Api = Awaited<ReturnType<typeof loadApi>>;

async function createDeps(db: Db, options: MigrateOptions): Promise<ApplyDeps> {
  const api = await loadApi();
  const repo = new api.BillingRepository(db);
  const users = new api.UserRepository(db);
  const logger = silentLogger();
  const clock = (): Date => new Date(options.now.getTime());
  return {
    db,
    repo,
    users,
    options,
    addMonthsUtc: api.addMonthsUtc,
    billing: new api.BillingService(repo, users, logger, clock),
    audit: new api.AuditService(new api.AdminAuditRepository(db), logger, clock),
  };
}

function adminContext(userId: string, step: string, reason: string): AdminMutationContext {
  return { actorId: ACTOR_ID, reason, auditId: auditIdFor(userId, step) };
}

/**
 * The `adminAudit` row of one billing step, written before the step: it is
 * idempotent by auditId (the same id the ledger entries carry), so a rerun
 * after a failure in between finds it and repairs the rest. The admin routes
 * use the same action names.
 */
async function auditBillingStep(
  deps: ApplyDeps,
  step: { userId: string; step: string; action: string; reason: string; after: Record<string, unknown> },
): Promise<void> {
  await deps.audit.record({
    auditId: auditIdFor(step.userId, step.step),
    actorId: ACTOR_ID,
    action: step.action,
    targetType: 'user',
    targetId: step.userId,
    after: step.after,
    reason: step.reason,
  });
}

async function applyProPlans(deps: ApplyDeps, steps: UserStep[], reason: string): Promise<void> {
  for (const { userId } of steps) {
    await auditBillingStep(deps, { userId, step: 'plan', action: 'billing.plan.set', reason, after: { planId: 'pro' } });
    await deps.billing.setPlan(userId, { planId: 'pro' }, adminContext(userId, 'plan', reason));
  }
}

async function applyLots(deps: ApplyDeps, steps: LotStep[], step: string, reason: string): Promise<void> {
  const expiresAt = deps.addMonthsUtc(deps.options.now, TERM_MONTHS);
  for (const { userId, minutes } of steps) {
    const after = { minutes, expiresAt: expiresAt.toISOString() };
    await auditBillingStep(deps, { userId, step, action: 'billing.credits.grant', reason, after });
    await deps.billing.grantLot(userId, { minutes, expiresAt }, adminContext(userId, step, reason));
  }
}

async function applyRecurring(deps: ApplyDeps, steps: LotStep[], step: string, reason: string): Promise<void> {
  for (const { userId, minutes } of steps) {
    const after = { minutes, every: 'month', active: true };
    await auditBillingStep(deps, { userId, step, action: 'billing.recurring.create', reason, after });
    await deps.billing.createRecurringGrant(userId, { minutes, every: 'month', active: true }, adminContext(userId, step, reason));
  }
}

/** The audit row goes first and is idempotent by auditId, so a rerun after a failed role write repairs it. */
async function applyRoles(deps: ApplyDeps, steps: RoleStep[]): Promise<void> {
  for (const step of steps) {
    const roles: Role[] = step.action === 'promote' ? ['admin'] : [];
    await deps.audit.record({
      auditId: auditIdFor(step.userId, 'role'),
      actorId: ACTOR_ID,
      action: `roles.migration.${step.action}`,
      targetType: 'role',
      targetId: step.userId,
      before: step.before,
      after: { roles, roleSource: roles.length > 0 ? 'env' : null },
      reason: 'migration: Q15 admin cleanup',
    });
    await deps.users.setRoles(step.userId, roles, 'env');
  }
}

async function applyPlan(deps: ApplyDeps, plan: MigrationPlan): Promise<void> {
  // summary() reads through getOrCreateAccount, which is how vie-api creates an account.
  for (const { userId } of plan.accounts) await deps.billing.summary(userId);
  await applyProPlans(deps, plan.pro, 'migration: pro tier');
  await applyProPlans(deps, plan.team, 'migration: team tier plan');
  await applyLots(deps, plan.teamLots, 'team-lot', 'migration: team tier');
  await applyRoles(deps, plan.roles);
  await applyLots(deps, plan.creditLots, 'credit-lot', `migration: unconsumed credit from ${deps.options.day}`);
  await applyRecurring(deps, plan.demoGrants, 'recurring-demo', 'migration: demo account monthly grant');
  await applyRecurring(deps, plan.evalGrants, 'recurring-eval', 'migration: eval account monthly grant');
}

/**
 * Accounts and roles are written outside any transaction, so on a standalone
 * server a run would stop part-way at the first ledger transaction. A replica
 * set member reports its set name in `hello`; check before anything else.
 */
async function assertReplicaSet(db: Db): Promise<void> {
  const hello = await db.admin().command({ hello: 1 });
  if (typeof hello.setName !== 'string') throw new StandaloneMongoError();
}

/**
 * Plan the migration from what is in the database now and, unless `dryRun`,
 * apply it. Throws NoAdminsRemainError before writing anything.
 */
export async function migrateBilling(db: Db, options: MigrateOptions): Promise<MigrationPlan> {
  if (options.adminEmails.length === 0) throw new Error('--admin-emails (or ADMIN_EMAILS) needs at least one email');
  await assertReplicaSet(db);
  const deps = await createDeps(db, options);
  const plan = await buildPlan(deps);
  if (plan.adminsAfter === 0) throw new NoAdminsRemainError();
  if (!options.dryRun) await applyPlan(deps, plan);
  return plan;
}

// ─── CLI ────────────────────────────────────────────────────────────────────

interface Cli {
  options: MigrateOptions;
  yesProd: boolean;
}

function splitEmails(values: string[]): string[] {
  const emails = values.flatMap((value) => value.split(/[\s,;]+/)).map(normalizeEmail);
  return [...new Set(emails.filter((email) => email.length > 0))];
}

export function parseCli(argv: string[], env: NodeJS.ProcessEnv, now: Date = new Date()): Cli | null {
  try {
    const { values } = parseArgs({
      args: argv,
      options: {
        'dry-run': { type: 'boolean', default: false },
        run: { type: 'boolean', default: false },
        'admin-emails': { type: 'string', multiple: true },
        'demo-email': { type: 'string' },
        'eval-emails': { type: 'string', multiple: true },
        day: { type: 'string' },
        'yes-prod': { type: 'boolean', default: false },
      },
      allowPositionals: false,
    });
    if (values['dry-run'] === values.run) return null;
    const day = values.day ?? utcDayKey(now);
    if (!/^\d{4}-\d{2}-\d{2}$/.test(day)) return null;
    const demo = normalizeEmail(values['demo-email'] ?? env.DEMO_USER_EMAIL ?? '');
    const envAdminEmails = splitEmails([env.ADMIN_EMAILS ?? '']);
    return {
      yesProd: values['yes-prod'] === true,
      options: {
        dryRun: values['dry-run'] === true,
        adminEmails: values['admin-emails'] ? splitEmails(values['admin-emails']) : envAdminEmails,
        envAdminEmails,
        demoEmail: demo.length > 0 ? demo : null,
        evalEmails: splitEmails(values['eval-emails'] ?? []),
        day,
        now,
      },
    };
  } catch {
    // parseArgs throws on unknown flags and stray positionals; the usage line is the useful answer.
    return null;
  }
}

function printEmails(title: string, steps: UserStep[]): void {
  if (steps.length === 0) return;
  console.log(`\n${title}`);
  for (const { email } of steps) console.log(`  ${email}`);
}

/**
 * Kept admins become `roleSource: 'env'`, and vie-api's boot seeding demotes an
 * `env` admin whose email is not on ADMIN_EMAILS. Show both lists and say so
 * when they differ.
 */
function printKeepList(options: MigrateOptions): void {
  const { onlyKept, onlyEnv, envEmpty } = compareKeepList(options.adminEmails, options.envAdminEmails);
  console.log(`\nKeep-list (--admin-emails): ${options.adminEmails.join(', ')}`);
  console.log(`ADMIN_EMAILS (boot seeding):  ${envEmpty ? '(empty: boot seeding skips an empty list and changes nobody)' : options.envAdminEmails.join(', ')}`);
  if (onlyKept.length === 0 && onlyEnv.length === 0) {
    console.log('The two lists match.');
    return;
  }
  console.log('WARNING: the two lists differ.');
  if (!envEmpty && onlyKept.length > 0) console.log(`  kept here, not on ADMIN_EMAILS (demoted at the next vie-api boot): ${onlyKept.join(', ')}`);
  if (onlyEnv.length > 0) console.log(`  on ADMIN_EMAILS, not kept here (not promoted by this script; boot seeding promotes them): ${onlyEnv.join(', ')}`);
}

function printSkipped(skipped: Skipped[]): void {
  if (skipped.length === 0) return;
  console.log(`\nSkipped (${skipped.length})`);
  for (const { email, why } of skipped) console.log(`  ${email}: ${why}`);
}

function isEmptyPlan(plan: MigrationPlan): boolean {
  const steps = [plan.accounts, plan.pro, plan.team, plan.teamLots, plan.roles, plan.creditLots, plan.demoGrants, plan.evalGrants];
  return steps.every((list) => list.length === 0);
}

function printReport(plan: MigrationPlan, options: MigrateOptions): void {
  const minutes = (steps: LotStep[]): string => `${steps.reduce((sum, step) => sum + step.minutes, 0)} minutes`;
  const kept = plan.roles.filter((step) => step.action === 'promote');
  const demoted = plan.roles.filter((step) => step.action === 'demote');
  console.log(`Billing migration (${options.dryRun ? 'DRY RUN' : 'RUN'}), credit day ${options.day} UTC, now ${options.now.toISOString()}`);
  console.table([
    { step: 'accounts to create', users: plan.accounts.length, detail: '' },
    { step: 'tier pro -> manual Pro, 12 months', users: plan.pro.length, detail: '' },
    { step: 'tier team -> Pro, 12 months', users: plan.team.length, detail: '' },
    { step: 'team lots (D10)', users: plan.teamLots.length, detail: minutes(plan.teamLots) },
    { step: 'admins kept (roleSource env)', users: kept.length, detail: '' },
    { step: 'admins demoted', users: demoted.length, detail: '' },
    { step: 'credit lots ($1 = 30 minutes)', users: plan.creditLots.length, detail: minutes(plan.creditLots) },
    { step: 'demo recurring grant', users: plan.demoGrants.length, detail: `${minutes(plan.demoGrants)}/month` },
    { step: 'eval recurring grants', users: plan.evalGrants.length, detail: `${minutes(plan.evalGrants)}/month in all` },
  ]);
  printEmails('Admins kept', kept);
  printEmails('Admins demoted', demoted);
  printEmails('Credit lots', plan.creditLots);
  printKeepList(options);
  printSkipped(plan.skipped);
  console.log(`\nActive admins after: ${plan.adminsAfter}`);
  if (options.dryRun) console.log('Dry run: nothing was written.');
  else console.log(isEmptyPlan(plan) ? 'Nothing to do: the migration is already complete.' : 'Done. Run it again to confirm: every count above should be 0.');
}

async function main(argv: string[]): Promise<void> {
  const cli = parseCli(argv, process.env);
  if (!cli) {
    console.error(USAGE);
    process.exitCode = 1;
    return;
  }
  const uri = process.env.MONGODB_URI || DEFAULT_URI;
  if (!cli.options.dryRun && !cli.yesProd && looksLikeProd(uri)) throw new ProdTargetError();

  const client = new MongoClient(uri);
  try {
    await client.connect();
    printReport(await migrateBilling(client.db(), cli.options), cli.options);
  } finally {
    await client.close();
  }
}

/** True only when this file is the process entry point, so tests can import it without running. */
function isEntryPoint(): boolean {
  const entry = process.argv[1];
  return entry !== undefined && import.meta.url === pathToFileURL(resolve(entry)).href;
}

if (isEntryPoint()) {
  main(process.argv.slice(2)).catch((error: unknown) => {
    console.error(`migrate-billing: ${error instanceof Error ? error.message : String(error)}`);
    process.exit(1);
  });
}
