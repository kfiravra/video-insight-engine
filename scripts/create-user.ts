/**
 * Create a user account. Never an admin.
 *
 * Registration is closed in production (ALLOW_REGISTRATION=false), so accounts
 * are created out-of-band with this script. It writes the same document
 * UserRepository.create writes for a self-registered user and never touches
 * `role`: account creation cannot grant vie-admin access (the retired
 * create-admin.ts made every invited user an admin).
 *
 * An existing email is left alone. `--reset-password` is the only way to change
 * an existing user's password, and an existing admin refuses a re-run without
 * it, so nobody's password changes silently.
 *
 * Usage:
 *   Production: a one-off container from the API image's builder stage, see
 *   docs/DEPLOY.md "Create the first user". Inside it:
 *     npx tsx create-user.ts <email> <password> [name] [--reset-password]
 *   Local (from the repo root; the api workspace has the drivers):
 *     pnpm --filter @vie/api exec tsx ../scripts/create-user.ts <email> <password> [name] [--reset-password]
 *
 * Env: MONGODB_URI (default mongodb://localhost:27017/video-insight-engine?directConnection=true).
 */

import { resolve } from 'node:path';
import { pathToFileURL } from 'node:url';
import bcrypt from 'bcrypt';
import { MongoClient } from 'mongodb';
import type { Db } from 'mongodb';
import type { UserDocument } from '../api/src/repositories/user.repository.js';

// Same cost as AuthService.register.
const BCRYPT_ROUNDS = 10;
const USAGE = 'Usage: create-user.ts <email> <password> [name] [--reset-password]';

export interface UpsertUserInput {
  email: string;
  password: string;
  name?: string;
  resetPassword?: boolean;
}

export type UpsertUserOutcome = 'created' | 'unchanged' | 'password-reset';

/** An existing admin's password only changes when the operator asks for it explicitly. */
export class AdminResetRequiredError extends Error {
  constructor(email: string) {
    super(`${email} is an admin account; its password is unchanged. Pass --reset-password to change it.`);
    this.name = 'AdminResetRequiredError';
  }
}

export function normalizeEmail(email: string): string {
  return email.trim().toLowerCase();
}

function buildNewUser(email: string, passwordHash: string, name: string, now: Date): Omit<UserDocument, '_id'> {
  return {
    email,
    passwordHash,
    name,
    preferences: { defaultSummarizedFolder: null, theme: 'system' },
    usage: { videosThisMonth: 0, videosResetAt: now },
    createdAt: now,
    updatedAt: now,
  };
}

/**
 * Create the user, or leave an existing one alone. `role` is never written, on
 * insert or on update: the insert document has no such field and the only update
 * sets `passwordHash` and `updatedAt`.
 */
export async function upsertUser(db: Db, input: UpsertUserInput): Promise<UpsertUserOutcome> {
  const { password, resetPassword = false } = input;
  const email = normalizeEmail(input.email);
  if (!email || !password) throw new Error('email and password are required');

  const users = db.collection<Omit<UserDocument, '_id'>>('users');
  const existing = await users.findOne({ email }, { projection: { role: 1 } });
  if (existing?.role === 'admin' && !resetPassword) throw new AdminResetRequiredError(email);
  if (existing && !resetPassword) return 'unchanged';

  const now = new Date();
  const passwordHash = await bcrypt.hash(password, BCRYPT_ROUNDS);
  if (existing) {
    await users.updateOne({ email }, { $set: { passwordHash, updatedAt: now } });
    return 'password-reset';
  }

  const name = input.name?.trim() || email.split('@')[0];
  // $setOnInsert keeps a concurrent create of the same email from being overwritten.
  const result = await users.updateOne(
    { email },
    { $setOnInsert: buildNewUser(email, passwordHash, name, now) },
    { upsert: true },
  );
  return result.upsertedCount === 1 ? 'created' : 'unchanged';
}

interface CliArgs extends UpsertUserInput {
  resetPassword: boolean;
}

function parseArgs(argv: string[]): CliArgs | null {
  const resetPassword = argv.includes('--reset-password');
  const [email, password, name, ...extra] = argv.filter((arg) => arg !== '--reset-password');
  if (!email || !password || extra.length > 0) return null;
  return { email, password, name, resetPassword };
}

const OUTCOME_MESSAGES: Record<UpsertUserOutcome, string> = {
  created: 'created',
  unchanged: 'exists, unchanged',
  'password-reset': 'exists, password reset',
};

async function main(argv: string[]): Promise<void> {
  const args = parseArgs(argv);
  if (!args) {
    console.error(USAGE);
    process.exitCode = 1;
    return;
  }

  const client = new MongoClient(process.env.MONGODB_URI || 'mongodb://localhost:27017/video-insight-engine?directConnection=true');
  try {
    await client.connect();
    const outcome = await upsertUser(client.db(), args);
    console.log(`${normalizeEmail(args.email)}: ${OUTCOME_MESSAGES[outcome]}`);
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
    console.error(`create-user: ${error instanceof Error ? error.message : String(error)}`);
    process.exit(1);
  });
}
