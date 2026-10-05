/**
 * Create (or reset) the shared demo account that demo mode signs visitors into.
 *
 * Runs inside the vie-api container so it uses the API's own `bcrypt` and
 * `mongodb` packages and reads the same env the API reads — the password
 * never appears on a command line or in shell history:
 *
 *   docker compose exec -T vie-api node --input-type=module < scripts/create-demo-user.mjs
 *
 * Requires DEMO_USER_EMAIL and DEMO_USER_PASSWORD to be set for vie-api.
 *
 * The account is a normal user: the same document `UserRepository.create`
 * writes for a self-registered user, with no `role` and no `tier` (free tier).
 * Running it again resets the password to the current DEMO_USER_PASSWORD and
 * cancels a pending account deletion, so it doubles as the recovery command.
 */
import bcrypt from 'bcrypt';
import { MongoClient } from 'mongodb';

// Same cost as AuthService.register.
const BCRYPT_ROUNDS = 10;

function fail(message) {
  console.error(`create-demo-user: ${message}`);
  process.exit(1);
}

const email = (process.env.DEMO_USER_EMAIL ?? '').trim().toLowerCase();
const password = process.env.DEMO_USER_PASSWORD ?? '';
const mongoUri = process.env.MONGODB_URI;

if (!email || !password) {
  fail('DEMO_USER_EMAIL and DEMO_USER_PASSWORD must both be set for vie-api. Add them to .env and recreate vie-api first.');
}
if (!mongoUri) {
  fail('MONGODB_URI is not set. Run this inside the vie-api container.');
}

const client = new MongoClient(mongoUri);
try {
  await client.connect();
  const users = client.db().collection('users');
  const existing = await users.findOne({ email }, { projection: { role: 1, deletedAt: 1 } });

  if (existing?.role === 'admin') {
    fail(`${email} is an admin account. Use a dedicated email for the demo user.`);
  }

  const now = new Date();
  const passwordHash = await bcrypt.hash(password, BCRYPT_ROUNDS);

  if (!existing) {
    await users.insertOne({
      email,
      passwordHash,
      name: 'Demo',
      preferences: { defaultSummarizedFolder: null, theme: 'system' },
      usage: { videosThisMonth: 0, videosResetAt: now },
      createdAt: now,
      updatedAt: now,
    });
    console.log(`Created demo user ${email}`);
  } else {
    // Mirrors UserRepository.clearSoftDelete; the API notices within its 30 s cache window.
    const restore = existing.deletedAt ? { deletedAt: null, hardDeleteAt: null } : {};
    await users.updateOne({ _id: existing._id }, { $set: { passwordHash, updatedAt: now, ...restore } });
    console.log(`Demo user ${email} already existed: password reset${existing.deletedAt ? ', pending deletion cancelled' : ''}`);
  }
} finally {
  await client.close();
}
