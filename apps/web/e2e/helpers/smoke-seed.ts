/**
 * Smoke-suite seeding — real backend, zero LLM cost.
 *
 * The smoke tests run against the REAL api (localhost:3000) + MongoDB
 * (localhost:27017) instead of route mocks. To avoid triggering the real
 * summarizer pipeline (which costs money), we never POST /api/videos.
 * Instead this helper:
 *
 *   1. Ensures a dedicated e2e user exists (login-first, register on miss —
 *      register is rate-limited to 5/hour so we only call it when needed).
 *   2. Inserts a minimal-but-valid COMPLETED video directly into MongoDB:
 *      - `videoSummaryCache` row with the v3 clean shape (`meta` + `tabs`)
 *        consumed by GET /api/videos/:id via buildMetaFromDoc/buildTabsFromDoc
 *        (api/src/utils/meta-builder.ts).
 *      - `userVideos` row linking the user to that cache row.
 *
 * Fixed ObjectIds make the seed idempotent (pure upserts) and give tests a
 * stable /video/:id URL.
 *
 * The mongodb driver is not a dependency of apps/web — it is resolved from
 * the api package's node_modules via createRequire, which keeps the web
 * package.json untouched.
 */
import * as fs from "node:fs";
import { createRequire } from "node:module";
import { fileURLToPath } from "node:url";

// ── Fixture constants ──

export const SMOKE_USER = {
  email: "e2e-smoke@vie.test",
  password: "E2eSmokePass123",
  name: "E2E Smoke",
};

/** 24-hex fixed ids so reruns upsert instead of duplicating. */
export const SMOKE_CACHE_ID = "e2ecac4e0000000000000001";
export const SMOKE_USER_VIDEO_ID = "e2ec1de00000000000000001";

export const SMOKE_YOUTUBE_ID = "e2eSm0keVid"; // 11 chars, never a real video
export const SMOKE_VIDEO_TITLE = "E2E Smoke Fixture Video";
export const SMOKE_VIDEO_PATH = `/video/${SMOKE_USER_VIDEO_ID}`;

export const SMOKE_TAB_LABELS = {
  moments: "Key Moments",
  checklist: "Action Checklist",
  concepts: "Concept Cards",
};

export const SMOKE_CHECKLIST_ITEM = "Review the summary before acting";

const API_URL = process.env.E2E_API_URL || "http://localhost:3000/api";
const MONGODB_URI =
  process.env.MONGODB_URI || "mongodb://localhost:27017/video-insight-engine?directConnection=true";

// ── Narrow structural types for the driver loaded via createRequire ──
// (mongodb is not in apps/web's dependency tree, so its types aren't either)

interface CollectionLike {
  updateOne(
    filter: Record<string, unknown>,
    update: Record<string, unknown>,
    options?: Record<string, unknown>,
  ): Promise<unknown>;
}

interface DbLike {
  collection(name: string): CollectionLike;
}

interface MongoClientLike {
  connect(): Promise<unknown>;
  db(): DbLike;
  close(): Promise<void>;
}

interface MongoModule {
  MongoClient: new (uri: string) => MongoClientLike;
  ObjectId: new (hex: string) => object;
}

function loadMongoDriver(): MongoModule {
  // Resolve mongodb from the api package (pnpm symlinks it there).
  // import.meta.url (not __dirname) — apps/web is an ESM package.
  const apiPackageJson = new URL(
    "../../../../api/package.json",
    import.meta.url,
  );
  const requireFromApi = createRequire(apiPackageJson);
  return requireFromApi("mongodb") as MongoModule;
}

// ── Auth: login-first, register on miss ──

interface AuthUser {
  id: string;
  email: string;
  name: string;
}

interface AuthResult {
  accessToken: string;
  user: AuthUser;
}

async function postJson(
  pathname: string,
  body: Record<string, unknown>,
): Promise<{ status: number; json: Record<string, unknown> }> {
  const res = await fetch(`${API_URL}${pathname}`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  const json = (await res.json().catch(() => ({}))) as Record<string, unknown>;
  return { status: res.status, json };
}

/** GET /auth/me with the token; null when the token is expired/invalid. */
async function validateToken(accessToken: string): Promise<AuthUser | null> {
  const res = await fetch(`${API_URL}/auth/me`, {
    headers: { Authorization: `Bearer ${accessToken}` },
  }).catch(() => null);
  if (!res || res.status !== 200) return null;
  return (await res.json()) as AuthUser;
}

export async function ensureSmokeUser(): Promise<AuthResult> {
  // Reuse the previous run's token while it is still valid — POST /auth/login
  // is IP-rate-limited (10/15min), so every avoided login buys local reruns.
  const cached = readSeedFile();
  if (cached) {
    const user = await validateToken(cached.accessToken);
    if (user) return { accessToken: cached.accessToken, user };
  }

  const login = await postJson("/auth/login", {
    email: SMOKE_USER.email,
    password: SMOKE_USER.password,
  });
  if (login.status === 200) {
    return login.json as unknown as AuthResult;
  }

  const register = await postJson("/auth/register", SMOKE_USER);
  if (register.status === 201) {
    return register.json as unknown as AuthResult;
  }

  throw new Error(
    `Smoke seed: could not login (${login.status}) or register (${register.status}) ` +
      `the e2e user against ${API_URL}. Is the api + mongo stack running?`,
  );
}

// ── Video fixture (v3 clean shape: meta + tabs) ──

function buildMeta(): Record<string, unknown> {
  return {
    videoId: SMOKE_YOUTUBE_ID,
    videoTitle: SMOKE_VIDEO_TITLE,
    creator: "VIE E2E",
    contentTags: ["learning"],
    modifiers: [],
    primaryTag: "learning",
    userGoal: "Verify the video page renders cached output",
    tldr: "A seeded fixture video used by the e2e smoke suite.",
    keyTakeaways: [
      "Cached videos render without any pipeline run",
      "Tabs come straight from the videoSummaryCache document",
    ],
    masterSummary:
      "This document is inserted directly into MongoDB by the e2e smoke seed. " +
      "It exercises the cache-serve path end to end without spending a single " +
      "LLM token.",
    seoDescription: "E2E smoke fixture video.",
  };
}

function buildTabs(): Record<string, unknown>[] {
  return [
    {
      id: "overview",
      label: "Overview",
      emoji: "🧭",
      component: "overview",
      props: {
        data: {
          title: SMOKE_VIDEO_TITLE,
          creator: "VIE E2E",
          duration: 300,
        },
      },
    },
    {
      id: "moments",
      label: SMOKE_TAB_LABELS.moments,
      emoji: "🎬",
      component: "moment_track",
      props: {
        items: [
          {
            time: "0:45",
            seconds: 45,
            label: "Setting the scene",
            description: "The fixture introduces itself.",
          },
          {
            time: "2:30",
            seconds: 150,
            label: "Main insight",
            description: "Cache-served videos cost $0.00.",
          },
        ],
      },
    },
    {
      id: "checklist",
      label: SMOKE_TAB_LABELS.checklist,
      emoji: "✅",
      component: "checklist",
      props: {
        items: [
          { label: SMOKE_CHECKLIST_ITEM },
          { label: "Confirm tabs render from cache" },
        ],
      },
    },
    {
      id: "concepts",
      label: SMOKE_TAB_LABELS.concepts,
      emoji: "🧠",
      component: "flash_deck",
      props: {
        cards: [
          { front: "What is VIE?", back: "Video Insight Engine" },
          { front: "What does this fixture cost?", back: "Zero LLM tokens" },
        ],
      },
    },
  ];
}

// ── Seed handoff file ──
// smoke.setup.ts seeds ONCE per run and hands the result to the parallel
// smoke.spec.ts workers through this gitignored file.

export interface SmokeSeedResult {
  accessToken: string;
  user: AuthUser;
}

const SEED_FILE = fileURLToPath(
  new URL("../.smoke-seed.json", import.meta.url),
);

export function writeSeedFile(seed: SmokeSeedResult): void {
  fs.writeFileSync(SEED_FILE, JSON.stringify(seed, null, 2), "utf-8");
}

export function readSeedFile(): SmokeSeedResult | null {
  try {
    return JSON.parse(fs.readFileSync(SEED_FILE, "utf-8")) as SmokeSeedResult;
  } catch {
    return null;
  }
}

export function requireSeedFile(): SmokeSeedResult {
  const seed = readSeedFile();
  if (!seed) {
    throw new Error(
      "Smoke seed file missing. Run through the smoke project so " +
        "smoke.setup.ts seeds first: npx playwright test --project=smoke",
    );
  }
  return seed;
}

export async function seedSmokeData(): Promise<SmokeSeedResult> {
  const auth = await ensureSmokeUser();

  const { MongoClient, ObjectId } = loadMongoDriver();
  const client = new MongoClient(MONGODB_URI);
  await client.connect();
  try {
    const db = client.db();
    const now = new Date();

    await db.collection("videoSummaryCache").updateOne(
      { _id: new ObjectId(SMOKE_CACHE_ID) },
      {
        $set: {
          youtubeId: SMOKE_YOUTUBE_ID,
          url: `https://www.youtube.com/watch?v=${SMOKE_YOUTUBE_ID}`,
          status: "completed",
          title: SMOKE_VIDEO_TITLE,
          channel: "VIE E2E",
          duration: 300,
          thumbnailUrl: `https://i.ytimg.com/vi/${SMOKE_YOUTUBE_ID}/hqdefault.jpg`,
          version: 1,
          isLatest: true,
          retryCount: 0,
          meta: buildMeta(),
          tabs: buildTabs(),
          processedAt: now,
          updatedAt: now,
        },
        $setOnInsert: { createdAt: now },
      },
      { upsert: true },
    );

    await db.collection("userVideos").updateOne(
      { _id: new ObjectId(SMOKE_USER_VIDEO_ID) },
      {
        $set: {
          userId: new ObjectId(auth.user.id),
          videoSummaryId: new ObjectId(SMOKE_CACHE_ID),
          youtubeId: SMOKE_YOUTUBE_ID,
          title: SMOKE_VIDEO_TITLE,
          channel: "VIE E2E",
          duration: 300,
          thumbnailUrl: `https://i.ytimg.com/vi/${SMOKE_YOUTUBE_ID}/hqdefault.jpg`,
          status: "completed",
          folderId: null,
          updatedAt: now,
        },
        $setOnInsert: { addedAt: now, createdAt: now },
      },
      { upsert: true },
    );
  } finally {
    await client.close();
  }

  return { accessToken: auth.accessToken, user: auth.user };
}
