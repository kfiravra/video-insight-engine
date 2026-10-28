/**
 * Demote every admin except the ones on a keep-list. One-off.
 *
 * Hosts provisioned with the retired create-admin.ts have an `admin` role on
 * every hand-created account, which gives each of those users the vie-admin
 * panel (every user's email and costs, global video purge, credit grants).
 * This unsets `role` on every admin whose email is not in --keep. It touches
 * nothing else: no other field, and no user that is not an admin.
 *
 * Refuses (exit 1, nothing written) when no --keep email belongs to a current
 * admin, since that would leave the panel with zero admins. Use --dry-run first.
 *
 * Usage:
 *   Production: a one-off container from the API image's builder stage, see
 *   docs/DEPLOY.md "One-off: demote hand-provisioned admins". Inside it:
 *     npx tsx demote-admins.ts --keep <email,…> [--dry-run]
 *   Local (from the repo root; the api workspace has the drivers):
 *     pnpm --filter @vie/api exec tsx ../scripts/demote-admins.ts --keep <email,…> [--dry-run]
 *
 * Env: MONGODB_URI (default mongodb://localhost:27017/video-insight-engine).
 */

import { resolve } from 'node:path';
import { pathToFileURL } from 'node:url';
import { parseArgs } from 'node:util';
import { MongoClient } from 'mongodb';
import type { Db } from 'mongodb';

const USAGE = 'Usage: demote-admins.ts --keep <email,…> [--dry-run]';

export interface DemoteAdminsInput {
  keep: string[];
  dryRun: boolean;
}

export interface DemoteAdminsResult {
  /** Admins demoted, or that would be on a dry run. */
  demoted: string[];
  /** Admins left as they are. */
  kept: string[];
  /** Keep-list emails that match no current admin. */
  unmatchedKeep: string[];
  /** Documents actually changed; always 0 on a dry run. */
  modifiedCount: number;
}

/** Raised before any write when the keep-list would leave the panel with no admin. */
export class NoAdminsKeptError extends Error {
  constructor() {
    super('none of the --keep emails is a current admin, so this would leave zero admins; nothing was changed.');
    this.name = 'NoAdminsKeptError';
  }
}

// Kept in this file rather than imported: the production runbook mounts a single script into the container.
function normalizeEmail(email: string): string {
  return email.trim().toLowerCase();
}

function normalizeKeepList(keep: string[]): string[] {
  return [...new Set(keep.map(normalizeEmail).filter((email) => email.length > 0))];
}

/**
 * Admin emails are matched case-insensitively against the keep-list but demoted
 * by their stored spelling, so a hand-written mixed-case address is still found.
 */
export async function demoteAdmins(db: Db, input: DemoteAdminsInput): Promise<DemoteAdminsResult> {
  const keepList = normalizeKeepList(input.keep);
  if (keepList.length === 0) throw new Error('--keep needs at least one email');

  const users = db.collection('users');
  const admins = await users.find({ role: 'admin' }, { projection: { email: 1 } }).toArray();
  const adminEmails = admins.map((admin) => admin.email).filter((email): email is string => typeof email === 'string');

  const keepSet = new Set(keepList);
  const kept = adminEmails.filter((email) => keepSet.has(normalizeEmail(email)));
  const demoted = adminEmails.filter((email) => !keepSet.has(normalizeEmail(email)));
  if (kept.length === 0) throw new NoAdminsKeptError();

  const adminSet = new Set(adminEmails.map(normalizeEmail));
  const unmatchedKeep = keepList.filter((email) => !adminSet.has(email));
  if (input.dryRun || demoted.length === 0) return { demoted, kept, unmatchedKeep, modifiedCount: 0 };

  const result = await users.updateMany({ role: 'admin', email: { $in: demoted } }, { $unset: { role: '' } });
  return { demoted, kept, unmatchedKeep, modifiedCount: result.modifiedCount };
}

function parseCli(argv: string[]): DemoteAdminsInput | null {
  try {
    const { values } = parseArgs({
      args: argv,
      options: { keep: { type: 'string', multiple: true }, 'dry-run': { type: 'boolean', default: false } },
      allowPositionals: false,
    });
    if (!values.keep) return null;
    return { keep: values.keep.flatMap((value) => value.split(',')), dryRun: values['dry-run'] === true };
  } catch {
    // parseArgs throws on unknown flags and stray positionals; the usage line is the useful answer.
    return null;
  }
}

function printList(title: string, emails: string[]): void {
  console.log(`${title} (${emails.length})`);
  for (const email of emails) console.log(`  ${email}`);
}

function printReport(result: DemoteAdminsResult, dryRun: boolean): void {
  for (const email of result.unmatchedKeep) console.warn(`warning: ${email} is not a current admin`);
  printList(dryRun ? 'Would demote' : 'Demoted', result.demoted);
  printList('Kept as admin', result.kept);
  console.log(dryRun ? 'Dry run: nothing was written.' : `Demoted ${result.modifiedCount} admin(s).`);
}

async function main(argv: string[]): Promise<void> {
  const input = parseCli(argv);
  if (!input) {
    console.error(USAGE);
    process.exitCode = 1;
    return;
  }

  const client = new MongoClient(process.env.MONGODB_URI || 'mongodb://localhost:27017/video-insight-engine');
  try {
    await client.connect();
    printReport(await demoteAdmins(client.db(), input), input.dryRun);
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
    console.error(`demote-admins: ${error instanceof Error ? error.message : String(error)}`);
    process.exit(1);
  });
}
