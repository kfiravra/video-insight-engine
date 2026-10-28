import { MongoMemoryReplSet } from 'mongodb-memory-server';
import { MongoClient, Db } from 'mongodb';
import { beforeAll, afterAll, beforeEach, vi } from 'vitest';

let mongod: MongoMemoryReplSet;
let client: MongoClient;
let db: Db;

// Start in-memory MongoDB before all tests. A single-node replica set, like dev
// and prod (docker-compose), so multi-document transactions work in tests.
beforeAll(async () => {
  mongod = await MongoMemoryReplSet.create({ replSet: { count: 1 } });
  const uri = mongod.getUri();
  client = new MongoClient(uri);
  await client.connect();
  db = client.db('test');
});

// Clean up after all tests
afterAll(async () => {
  if (client) {
    await client.close();
  }
  if (mongod) {
    await mongod.stop();
  }
});

// Clear all collections before each test
beforeEach(async () => {
  const collections = await db.listCollections().toArray();
  for (const collection of collections) {
    await db.collection(collection.name).deleteMany({});
  }
});

// Export for test files
export function getTestDb(): Db {
  return db;
}

export function getTestClient(): MongoClient {
  return client;
}

// Mock logger for tests
export const mockLogger = {
  info: vi.fn(),
  error: vi.fn(),
  warn: vi.fn(),
  debug: vi.fn(),
  trace: vi.fn(),
  fatal: vi.fn(),
  child: vi.fn(() => mockLogger),
  level: 'silent',
  silent: vi.fn(),
};
