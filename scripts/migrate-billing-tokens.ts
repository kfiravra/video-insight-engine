/**
 * One-off token migration (billing-tokens): converts the minutes ledger to the
 * token ledger in place. What it does per account is decided in
 * migrate-billing-tokens-plan.ts (read the header there). Idempotent: a second
 * run finds nothing to do and prints zeros.
 *
 * Each account converts in one transaction, re-planned from what is stored at
 * that moment: `convert` entries close the minute balances (minute entries,
 * no `unit`) and open their token counterparts (`unit: 'tokens'`), new token
 * lots replace the open minute lots, the plan bucket and the account's token
 * fields are set, the minute fields unset, and the signup grant written where
 * it is missing. Users without an account get one through vie-api's
 * `BillingService.ensureAccount` (which writes the signup grant). Recurring
 * grants gain `tokens`. `billingShadow` and the minute-era lot indexes are
 * dropped. Every account is drift-checked (vie-api's `verifyAccountById`)
 * after the run.
 *
 * Refuses (exit 1, nothing written, dry run included) on a standalone Mongo,
 * and `--run` refuses while any minute hold is still open (a run in flight
 * at the deploy: wait for it). `--run` against a production-looking URI
 * needs --yes-prod.
 *
 * Usage (production: docs/DEPLOY.md "Migrate to the token ledger"; the scripts
 * are mounted into /app/api in a one-off container):
 *   npx tsx migrate-billing-tokens.ts (--dry-run | --run) [--yes-prod]
 *   Local: pnpm --filter @vie/api exec tsx ../scripts/migrate-billing-tokens.ts --dry-run
 *
 * Env: MONGODB_URI (default mongodb://localhost:27017/video-insight-engine?directConnection=true).
 */

import { resolve } from 'node:path';
import { pathToFileURL } from 'node:url';
import { parseArgs } from 'node:util';
import { MongoClient, ObjectId } from 'mongodb';
import type { ClientSession, Db } from 'mongodb';
import type { FastifyBaseLogger } from 'fastify';
import {
  MIGRATION_ACTOR,
  MIGRATION_REASON,
  MIGRATION_REF,
  buildTokenPlan,
  convertKey,
  planAccount,
  readAccountInput,
  signupKey,
} from './migrate-billing-tokens-plan.js';
import type { AccountConversion, RawDoc, TokenConstants, TokenMigrationPlan } from './migrate-billing-tokens-plan.js';

export { MINUTES_TO_TOKENS, tokensForMinutes } from './migrate-billing-tokens-plan.js';
export type { TokenMigrationPlan } from './migrate-billing-tokens-plan.js';

const USAGE = 'Usage: migrate-billing-tokens.ts (--dry-run | --run) [--yes-prod]';
const DEFAULT_URI = 'mongodb://localhost:27017/video-insight-engine?directConnection=true';
const LOCAL_HOSTS = new Set(['localhost', '127.0.0.1', '::1', '[::1]', 'vie-mongodb']);

export interface TokenMigrateOptions {
  dryRun: boolean;
  now: Date;
}

/** What a run did (all zeros on a second run) and the checks after it. */
export interface TokenMigrationResult {
  plan: TokenMigrationPlan;
  accountsConverted: number;
  accountsCreated: number;
  signupGrants: number;
  recurringGrantsConverted: number;
  /** Accounts whose projection does not equal their ledger after the run (should be none). */
  drift: Array<{ accountId: string; fields: string[] }>;
}

/** Raised before anything is read or written when Mongo is a standalone server: the ledger needs transactions. */
export class StandaloneMongoError extends Error {
  constructor() {
    super(
      'MongoDB is a standalone server (hello has no setName) and the token ledger needs a replica set for transactions; nothing was changed.',
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

/** Raised before any write while minute holds (runs in flight) are open. */
export class OpenMinuteHoldsError extends Error {
  constructor(readonly holds: number) {
    super(`${holds} minute hold(s) are still open (runs in flight at the deploy); wait for them to finish, then run again. Nothing was changed.`);
    this.name = 'OpenMinuteHoldsError';
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

// ─── vie-api, loaded at run time ───

function silentLogger(): FastifyBaseLogger {
  const noop = (): void => undefined;
  const logger = { info: noop, warn: noop, error: noop, debug: noop, trace: noop, fatal: noop, silent: noop, level: 'silent' };
  return Object.assign(logger, { child: () => logger }) as unknown as FastifyBaseLogger;
}

/**
 * vie-api's classes and @vie/types' constants, loaded with import(): scripts/
 * has no "type": "module", so tsx compiles this file as CommonJS, where a
 * static import of the ESM sources would resolve @vie/types by its `require`
 * conditions and fail. import() keeps them ESM.
 */
async function loadApi() {
  const [types, billingRepo, userRepo, billingService] = await Promise.all([
    import('@vie/types'),
    import('../api/src/repositories/billing.repository.js'),
    import('../api/src/repositories/user.repository.js'),
    import('../api/src/services/billing/billing.service.js'),
  ]);
  return { ...billingRepo, ...userRepo, ...billingService, PLANS: types.PLANS, SIGNUP_GRANT_TOKENS: types.SIGNUP_GRANT_TOKENS };
}

type Api = Awaited<ReturnType<typeof loadApi>>;

function constantsOf(api: Api): TokenConstants {
  return { proPeriodTokens: api.PLANS.pro.periodTokens, signupGrantTokens: api.SIGNUP_GRANT_TOKENS };
}

function billingService(db: Db, api: Api, now: Date) {
  const clock = (): Date => new Date(now.getTime());
  return new api.BillingService(new api.BillingRepository(db), new api.UserRepository(db), silentLogger(), clock);
}

// ─── Writing one account ───

interface EntryBase {
  accountId: ObjectId;
  userId: ObjectId;
  source: { kind: 'migration'; ref: string };
  actorId: string;
  reason: string;
  createdAt: Date;
}

/** Insert a ledger entry unless its key exists (a duplicate inside a transaction would abort it). */
async function insertEntry(db: Db, entry: Record<string, unknown>, session: ClientSession): Promise<void> {
  const ledger = db.collection('ledgerEntries');
  if (await ledger.findOne({ idempotencyKey: entry.idempotencyKey }, { session })) return;
  await ledger.insertOne({ _id: new ObjectId(), ...entry }, { session });
}

async function convertLots(db: Db, plan: AccountConversion, base: EntryBase, now: Date, session: ClientSession): Promise<void> {
  for (const lot of plan.lots) {
    const what = `lot:${lot.lotId.toHexString()}`;
    const newLotId = lot.tokens > 0 ? new ObjectId() : null;
    await insertEntry(db, { ...base, type: 'convert', bucket: 'credit', minutes: -lot.minutesRemaining, lotId: lot.lotId, idempotencyKey: convertKey(plan.accountId, what) }, session);
    if (newLotId) {
      await db.collection('creditLots').insertOne(
        {
          _id: newLotId, accountId: plan.accountId, tokensGranted: lot.tokens, tokensRemaining: lot.tokens,
          source: { kind: 'migration', ref: lot.lotId.toHexString() }, expiresAt: lot.expiresAt, createdAt: now,
        },
        { session },
      );
      await insertEntry(db, { ...base, unit: 'tokens', type: 'convert', bucket: 'credit', tokens: lot.tokens, lotId: newLotId, idempotencyKey: convertKey(plan.accountId, `${what}:tokens`) }, session);
    }
    await db.collection('creditLots').updateOne(
      { _id: lot.lotId },
      { $set: { minutesRemaining: 0, ...(newLotId && { convertedToLotId: newLotId }) } },
      { session },
    );
  }
}

async function convertPlanBucket(db: Db, plan: AccountConversion, base: EntryBase, session: ClientSession): Promise<void> {
  const bucket = plan.plan;
  if (!bucket) return;
  if (bucket.minutesLeft > 0) {
    await insertEntry(db, { ...base, type: 'convert', bucket: 'plan', minutes: -bucket.minutesLeft, idempotencyKey: convertKey(plan.accountId, 'plan') }, session);
  }
  const tokensLeft = bucket.tokensGranted - bucket.tokensUsed;
  if (tokensLeft > 0) {
    await insertEntry(db, { ...base, unit: 'tokens', type: 'convert', bucket: 'plan', tokens: tokensLeft, idempotencyKey: convertKey(plan.accountId, 'plan:tokens') }, session);
  }
}

async function writeSignupGrant(db: Db, plan: AccountConversion, constants: TokenConstants, now: Date, session: ClientSession): Promise<void> {
  if (!plan.signupGrant) return;
  const lotId = new ObjectId();
  const source = { kind: 'system', ref: 'signup' };
  const tokens = constants.signupGrantTokens;
  await db.collection('creditLots').insertOne(
    { _id: lotId, accountId: plan.accountId, tokensGranted: tokens, tokensRemaining: tokens, source, expiresAt: null, createdAt: now },
    { session },
  );
  await insertEntry(
    db,
    { accountId: plan.accountId, userId: plan.ownerId, unit: 'tokens', type: 'grant', bucket: 'credit', tokens, lotId, source, idempotencyKey: signupKey(plan.ownerId), createdAt: now },
    session,
  );
}

/** The account document's token fields set, its minute fields unset. */
async function updateAccount(db: Db, account: RawDoc, plan: AccountConversion, now: Date, session: ClientSession): Promise<void> {
  const buckets = (account.buckets ?? {}) as Record<string, Record<string, unknown>>;
  const set: Record<string, unknown> = {
    'buckets.credit.tokensAvailable': plan.creditTokensAfter,
    'buckets.debt': { tokens: typeof buckets.debt?.tokens === 'number' ? buckets.debt.tokens : 0 },
    updatedAt: now,
  };
  if (plan.plan) {
    const { cycleStart = null, cycleEnd = null } = buckets.plan ?? {};
    set['buckets.plan'] = { cycleStart, cycleEnd, tokensGranted: plan.plan.tokensGranted, tokensUsed: plan.plan.tokensUsed };
  }
  if (plan.signupGrant) set.signupGrantAt = now;
  const unset = Object.fromEntries(plan.legacyFields.map((field) => [field, '']));
  const result = await db.collection('billingAccounts').updateOne(
    { _id: account._id, version: account.version },
    { $set: set, ...(Object.keys(unset).length > 0 && { $unset: unset }), $inc: { version: 1 } },
    { session },
  );
  if (result.matchedCount === 0) throw new Error(`billing account ${account._id.toHexString()} changed during the migration; run it again`);
}

/** Convert one account in one transaction, re-planned from what is stored now. Returns its plan (null if gone). */
async function convertAccount(
  db: Db,
  accountId: ObjectId,
  constants: TokenConstants,
  now: Date,
): Promise<AccountConversion | null> {
  const session = db.client.startSession();
  try {
    return await session.withTransaction(async () => {
      const account = await db.collection<RawDoc>('billingAccounts').findOne({ _id: accountId }, { session });
      if (!account) return null;
      const plan = planAccount(await readAccountInput(db, account, constants, now, session));
      if (plan.done) return plan;
      if (plan.openMinuteHolds.length > 0) throw new OpenMinuteHoldsError(plan.openMinuteHolds.length);
      const base: EntryBase = {
        accountId, userId: plan.ownerId, source: { kind: 'migration', ref: MIGRATION_REF },
        actorId: MIGRATION_ACTOR, reason: MIGRATION_REASON, createdAt: now,
      };
      await convertLots(db, plan, base, now, session);
      await convertPlanBucket(db, plan, base, session);
      await writeSignupGrant(db, plan, constants, now, session);
      await updateAccount(db, account, plan, now, session);
      return plan;
    });
  } finally {
    await session.endSession();
  }
}

async function dropLegacy(db: Db, plan: TokenMigrationPlan): Promise<void> {
  for (const name of plan.minuteIndexes) await db.collection('creditLots').dropIndex(name);
  if (plan.shadowRows > 0 || (await db.listCollections({ name: 'billingShadow' }, { nameOnly: true }).hasNext())) {
    await db.collection('billingShadow').drop();
  }
}

async function convertRecurringGrants(db: Db, plan: TokenMigrationPlan, now: Date): Promise<number> {
  let converted = 0;
  for (const grant of plan.recurringGrants) {
    const result = await db
      .collection('recurringGrants')
      .updateOne({ _id: grant.grantId, tokens: { $exists: false } }, { $set: { tokens: grant.tokens, updatedAt: now } });
    converted += result.modifiedCount;
  }
  return converted;
}

async function assertReplicaSet(db: Db): Promise<void> {
  const hello = await db.admin().command({ hello: 1 });
  if (typeof hello.setName !== 'string') throw new StandaloneMongoError();
}

/**
 * Plan the migration from what is in the database now and, unless `dryRun`,
 * apply it. Throws OpenMinuteHoldsError (on --run) before writing anything.
 */
export async function migrateBillingTokens(db: Db, options: TokenMigrateOptions): Promise<TokenMigrationResult> {
  const { dryRun, now } = options;
  await assertReplicaSet(db);
  const api = await loadApi();
  const constants = constantsOf(api);
  const plan = await buildTokenPlan(db, constants, now);
  const result: TokenMigrationResult = {
    plan, accountsConverted: 0, accountsCreated: 0, signupGrants: 0, recurringGrantsConverted: 0, drift: [],
  };
  if (dryRun) return result;
  const openHolds = plan.accounts.reduce((sum, account) => sum + account.openMinuteHolds.length, 0);
  if (openHolds > 0) throw new OpenMinuteHoldsError(openHolds);

  for (const account of plan.accounts) {
    if (account.done) continue;
    const converted = await convertAccount(db, account.accountId, constants, now);
    if (converted && !converted.done) {
      result.accountsConverted++;
      if (converted.signupGrant) result.signupGrants++;
    }
  }
  const billing = billingService(db, api, now);
  for (const { userId } of plan.newAccounts) {
    await billing.ensureAccount(userId);
    result.accountsCreated++;
    result.signupGrants++;
  }
  result.recurringGrantsConverted = await convertRecurringGrants(db, plan, now);
  await dropLegacy(db, plan);
  result.drift = await driftAfter(db, billing);
  return result;
}

async function driftAfter(db: Db, billing: ReturnType<typeof billingService>): Promise<TokenMigrationResult['drift']> {
  const accounts = await db.collection<RawDoc>('billingAccounts').find({}, { projection: { _id: 1 } }).toArray();
  const drift: TokenMigrationResult['drift'] = [];
  for (const { _id } of accounts) {
    const check = await billing.verifyAccountById(_id);
    if (!check.ok) drift.push({ accountId: _id.toHexString(), fields: check.drift.map((d) => d.field) });
  }
  return drift;
}

// ─── Report ───

function row(cells: Array<string | number>): string {
  const widths = [26, 6, 10, 10, 12, 12, 7, 6];
  return cells.map((cell, i) => String(cell).padEnd(widths[i] ?? 10)).join(' ');
}

/** The dry-run / run table: per account, before (minutes) and after (tokens). */
export function formatReport(result: TokenMigrationResult, dryRun: boolean): string {
  const { plan } = result;
  const lines = [
    dryRun ? 'DRY RUN — nothing was written.' : 'RUN — written.',
    '',
    row(['account (email)', 'plan', 'plan min', 'credit min', 'plan tokens', 'credit tok', 'signup', 'holds']),
  ];
  for (const account of plan.accounts) {
    const creditMinutes = account.lots.reduce((sum, lot) => sum + lot.minutesRemaining, 0);
    lines.push(
      row([
        account.email ?? `(no user) ${account.ownerId.toHexString().slice(0, 8)}`,
        account.planId,
        account.plan?.minutesLeft ?? '-',
        creditMinutes,
        account.planTokensAfter,
        account.creditTokensAfter,
        account.signupGrant ? 'yes' : 'no',
        account.openMinuteHolds.length,
      ]),
    );
  }
  lines.push('');
  lines.push(`New accounts (with the signup grant): ${plan.newAccounts.length}${plan.newAccounts.length ? ` — ${plan.newAccounts.map((user) => user.email).join(', ')}` : ''}`);
  for (const grant of plan.recurringGrants) {
    lines.push(`Recurring grant ${grant.grantId.toHexString()}: ${grant.minutes} min/month → ${grant.tokens} tokens/month`);
  }
  lines.push(`billingShadow rows dropped: ${plan.shadowRows}; minute lot indexes dropped: ${plan.minuteIndexes.join(', ') || 'none'}`);
  if (!dryRun) {
    lines.push(
      `Converted ${result.accountsConverted} account(s), created ${result.accountsCreated}, wrote ${result.signupGrants} signup grant(s), converted ${result.recurringGrantsConverted} recurring grant(s).`,
    );
    lines.push(result.drift.length === 0 ? 'Drift check: every account equals its ledger.' : `DRIFT: ${JSON.stringify(result.drift)}`);
  }
  return lines.join('\n');
}

// ─── CLI ───

export interface TokenCli {
  dryRun: boolean;
  yesProd: boolean;
}

export function parseCli(argv: string[]): TokenCli {
  const { values } = parseArgs({
    args: argv,
    options: { 'dry-run': { type: 'boolean' }, run: { type: 'boolean' }, 'yes-prod': { type: 'boolean' } },
    strict: true,
  });
  if (Boolean(values['dry-run']) === Boolean(values.run)) throw new Error(`pass exactly one of --dry-run / --run\n${USAGE}`);
  return { dryRun: Boolean(values['dry-run']), yesProd: Boolean(values['yes-prod']) };
}

async function main(): Promise<void> {
  const cli = parseCli(process.argv.slice(2));
  const uri = process.env.MONGODB_URI ?? DEFAULT_URI;
  if (!cli.dryRun && looksLikeProd(uri) && !cli.yesProd) throw new ProdTargetError();
  const client = new MongoClient(uri);
  try {
    await client.connect();
    const result = await migrateBillingTokens(client.db(), { dryRun: cli.dryRun, now: new Date() });
    console.log(formatReport(result, cli.dryRun));
    if (result.drift.length > 0) process.exitCode = 1;
  } finally {
    await client.close();
  }
}

function invokedDirectly(): boolean {
  const entry = process.argv[1];
  return entry !== undefined && import.meta.url === pathToFileURL(resolve(entry)).href;
}

if (invokedDirectly()) {
  main().catch((error: unknown) => {
    console.error(`migrate-billing-tokens: ${error instanceof Error ? error.message : String(error)}`);
    process.exit(1);
  });
}
