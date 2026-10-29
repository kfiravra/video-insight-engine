import { FastifyInstance } from 'fastify';
import { vi } from 'vitest';
import { buildApp } from '../app.js';
import * as softDeleteCache from '../utils/soft-delete-cache.js';
import { config } from '../config.js';
import { sha256Hex, signActorHeader } from '../utils/admin-actor.js';

// Mock container services for route testing
export interface MockContainer {
  videoRepository: {
    findCacheKeysByYoutubeId: ReturnType<typeof vi.fn>;
    findKnownDuration: ReturnType<typeof vi.fn>;
    userHasAccessToSummary: ReturnType<typeof vi.fn>;
    userOwnsVideo: ReturnType<typeof vi.fn>;
    findUserVideo: ReturnType<typeof vi.fn>;
    findCacheById: ReturnType<typeof vi.fn>;
    updateCacheEntry: ReturnType<typeof vi.fn>;
    tryClaimDispatchRelease: ReturnType<typeof vi.fn>;
    getUserVideos: ReturnType<typeof vi.fn>;
    mergeSynthesis: ReturnType<typeof vi.fn>;
    promoteCompletedVersion: ReturnType<typeof vi.fn>;
    restoreServedVersion: ReturnType<typeof vi.fn>;
  };
  videoService: {
    createVideo: ReturnType<typeof vi.fn>;
    getVideos: ReturnType<typeof vi.fn>;
    getVideo: ReturnType<typeof vi.fn>;
    deleteVideo: ReturnType<typeof vi.fn>;
    moveToFolder: ReturnType<typeof vi.fn>;
    userOwnsVideo: ReturnType<typeof vi.fn>;
    getVersions: ReturnType<typeof vi.fn>;
    overrideCategory: ReturnType<typeof vi.fn>;
  };
  folderService: {
    list: ReturnType<typeof vi.fn>;
    getById: ReturnType<typeof vi.fn>;
    create: ReturnType<typeof vi.fn>;
    update: ReturnType<typeof vi.fn>;
    delete: ReturnType<typeof vi.fn>;
  };
  authService: {
    register: ReturnType<typeof vi.fn>;
    login: ReturnType<typeof vi.fn>;
    getUser: ReturnType<typeof vi.fn>;
  };
  playlistService: {
    preview: ReturnType<typeof vi.fn>;
    import: ReturnType<typeof vi.fn>;
    getPlaylistVideos: ReturnType<typeof vi.fn>;
  };
  assistantClient: {
    chat: ReturnType<typeof vi.fn>;
    action: ReturnType<typeof vi.fn>;
  };
  summarizerClient: {
    triggerSummarization: ReturnType<typeof vi.fn>;
  };
  queuePublisher: {
    publishVideoJob: ReturnType<typeof vi.fn>;
  };
  shareService: {
    createShare: ReturnType<typeof vi.fn>;
    getPublicSummary: ReturnType<typeof vi.fn>;
    likeShare: ReturnType<typeof vi.fn>;
  };
  ogImageService: {
    getOgImage: ReturnType<typeof vi.fn>;
  };
  billingService: {
    summary: ReturnType<typeof vi.fn>;
  };
  billingEnforcement: {
    admit: ReturnType<typeof vi.fn>;
    submitResolved: ReturnType<typeof vi.fn>;
    submitFailed: ReturnType<typeof vi.fn>;
    runFinished: ReturnType<typeof vi.fn>;
    planPlaylist: ReturnType<typeof vi.fn>;
    refuseIfBlocked: ReturnType<typeof vi.fn>;
  };
  libraryUpdateRepository: {
    restoreFailedUpdates: ReturnType<typeof vi.fn>;
    clearSettledUpdates: ReturnType<typeof vi.fn>;
  };
  shareRepository: {
    findBySlug: ReturnType<typeof vi.fn>;
    markAsShared: ReturnType<typeof vi.fn>;
    incrementViews: ReturnType<typeof vi.fn>;
    hasLiked: ReturnType<typeof vi.fn>;
    addLike: ReturnType<typeof vi.fn>;
    getShareInfo: ReturnType<typeof vi.fn>;
  };
  idempotencyService: {
    computeKey: ReturnType<typeof vi.fn>;
    computeContentKey: ReturnType<typeof vi.fn>;
    findHit: ReturnType<typeof vi.fn>;
    reserveHash: ReturnType<typeof vi.fn>;
    completeHash: ReturnType<typeof vi.fn>;
    invalidateByHash: ReturnType<typeof vi.fn>;
    invalidateStaleCompleted: ReturnType<typeof vi.fn>;
    invalidateByVideoSummaryId: ReturnType<typeof vi.fn>;
  };
  dispatchGuardService: {
    acquire: ReturnType<typeof vi.fn>;
    release: ReturnType<typeof vi.fn>;
  };
  submitBurstLimiter: {
    consume: ReturnType<typeof vi.fn>;
  };
  chatFairUse: {
    consume: ReturnType<typeof vi.fn>;
  };
  idempotencyRepository: {
    findByHash: ReturnType<typeof vi.fn>;
    reserveHash: ReturnType<typeof vi.fn>;
    completeHash: ReturnType<typeof vi.fn>;
    invalidateByHash: ReturnType<typeof vi.fn>;
    invalidateStaleCompleted: ReturnType<typeof vi.fn>;
    invalidateByVideoSummaryId: ReturnType<typeof vi.fn>;
  };
  userRepository: {
    findById: ReturnType<typeof vi.fn>;
    findByEmail: ReturnType<typeof vi.fn>;
    findByUsername: ReturnType<typeof vi.fn>;
    create: ReturnType<typeof vi.fn>;
    update: ReturnType<typeof vi.fn>;
    updateLastLogin: ReturnType<typeof vi.fn>;
    markSoftDeleted: ReturnType<typeof vi.fn>;
    clearSoftDelete: ReturnType<typeof vi.fn>;
    findExpiredSoftDeletes: ReturnType<typeof vi.fn>;
    hardDelete: ReturnType<typeof vi.fn>;
    hasPermission: ReturnType<typeof vi.fn>;
    listAdmins: ReturnType<typeof vi.fn>;
  };
  userDeletionRepository: {
    insert: ReturnType<typeof vi.fn>;
    findByOriginalUserId: ReturnType<typeof vi.fn>;
    findByEmailHash: ReturnType<typeof vi.fn>;
  };
  userDeletionService: {
    requestDeletion: ReturnType<typeof vi.fn>;
    cancelDeletion: ReturnType<typeof vi.fn>;
    executeHardDelete: ReturnType<typeof vi.fn>;
    runScheduledDeletions: ReturnType<typeof vi.fn>;
  };
  videoCascadeService: {
    deleteVideo: ReturnType<typeof vi.fn>;
  };
  auditService: {
    record: ReturnType<typeof vi.fn>;
    recordBestEffort: ReturnType<typeof vi.fn>;
    list: ReturnType<typeof vi.fn>;
  };
  adminSeedService: {
    run: ReturnType<typeof vi.fn>;
  };
  adminReplayGuard: {
    claim: ReturnType<typeof vi.fn>;
  };
  alertConfigService: {
    replace: ReturnType<typeof vi.fn>;
  };
}

export function createMockContainer(): MockContainer {
  return {
    videoRepository: {
      findCacheKeysByYoutubeId: vi.fn().mockResolvedValue([]),
      // Default: no completed version knows the length, so submits ask the pre-flight.
      findKnownDuration: vi.fn().mockResolvedValue(null),
      userHasAccessToSummary: vi.fn().mockResolvedValue(true),
      userOwnsVideo: vi.fn().mockResolvedValue(true),
      findUserVideo: vi.fn(),
      findCacheById: vi.fn(),
      updateCacheEntry: vi.fn(),
      // Default: claim succeeds, so the FAILED-handler tests that don't
      // override this continue to see `dispatchGuardService.release` called.
      tryClaimDispatchRelease: vi.fn().mockResolvedValue(true),
      getUserVideos: vi.fn().mockResolvedValue([]),
      mergeSynthesis: vi.fn().mockResolvedValue(undefined),
      promoteCompletedVersion: vi.fn().mockResolvedValue(false),
      restoreServedVersion: vi.fn().mockResolvedValue(false),
    },
    videoService: {
      createVideo: vi.fn(),
      getVideos: vi.fn().mockResolvedValue({ videos: [], total: 0 }),
      getVideo: vi.fn(),
      deleteVideo: vi.fn(),
      moveToFolder: vi.fn(),
      userOwnsVideo: vi.fn(),
      getVersions: vi.fn(),
      overrideCategory: vi.fn(),
    },
    folderService: {
      list: vi.fn(),
      getById: vi.fn(),
      create: vi.fn(),
      update: vi.fn(),
      delete: vi.fn(),
    },
    authService: {
      register: vi.fn(),
      login: vi.fn(),
      getUser: vi.fn(),
    },
    playlistService: {
      preview: vi.fn(),
      import: vi.fn(),
      getPlaylistVideos: vi.fn(),
    },
    assistantClient: {
      chat: vi.fn(),
      action: vi.fn(),
    },
    summarizerClient: {
      triggerSummarization: vi.fn(),
    },
    queuePublisher: {
      publishVideoJob: vi.fn().mockResolvedValue({ requestId: 'test-req-1' }),
    },
    shareService: {
      createShare: vi.fn(),
      getPublicSummary: vi.fn(),
      likeShare: vi.fn(),
    },
    ogImageService: {
      getOgImage: vi.fn(),
    },
    // Route tests never touch the real ledger: every account is on free and
    // every submit is admitted with a free-video hold. Ledger behaviour on the
    // wire is billing-enforce.routes.test.ts, over a real LedgerEnforcement.
    billingService: {
      summary: vi.fn().mockResolvedValue({ plan: { id: 'free', periodEnd: null } }),
    },
    billingEnforcement: {
      admit: vi.fn().mockResolvedValue({ requestId: 'test-run', minutes: 1, split: { free: 1, plan: 0, credit: 0, lots: [] } }),
      submitResolved: vi.fn().mockResolvedValue(undefined),
      submitFailed: vi.fn().mockResolvedValue(undefined),
      runFinished: vi.fn().mockResolvedValue(undefined),
      planPlaylist: vi.fn().mockImplementation(async (_userId: string, items: Array<{ durationSec: number | null }>) => ({
        quote: { neededMinutes: 0, availableMinutes: 0, freeVideoAvailable: true, nextFreeAt: null, decisions: [] },
        durationsSec: items.map((item) => item.durationSec),
      })),
      // Default: not blocked, so an idempotency replay is answered.
      refuseIfBlocked: vi.fn().mockResolvedValue(undefined),
    },
    // Default: no entry an update moved onto the row.
    libraryUpdateRepository: {
      restoreFailedUpdates: vi.fn().mockResolvedValue({ restored: 0, stranded: 0 }),
      clearSettledUpdates: vi.fn().mockResolvedValue(0),
    },
    shareRepository: {
      findBySlug: vi.fn(),
      markAsShared: vi.fn(),
      incrementViews: vi.fn(),
      hasLiked: vi.fn().mockResolvedValue(false),
      addLike: vi.fn().mockResolvedValue(1),
      getShareInfo: vi.fn(),
    },
    idempotencyService: {
      computeKey: vi.fn().mockReturnValue('test-hash'),
      computeContentKey: vi.fn().mockReturnValue('test-content-key'),
      findHit: vi.fn().mockResolvedValue(null),
      reserveHash: vi.fn().mockResolvedValue({
        created: true,
        doc: {
          _id: { toString: () => 'idem-default' },
          hash: 'test-hash',
          status: 'pending',
          userId: { toString: () => 'test-user-id' },
          youtubeId: 'dQw4w9WgXcQ',
        },
      }),
      completeHash: vi.fn().mockResolvedValue(undefined),
      invalidateByHash: vi.fn().mockResolvedValue(undefined),
      invalidateStaleCompleted: vi.fn().mockResolvedValue(undefined),
      invalidateByVideoSummaryId: vi.fn().mockResolvedValue(undefined),
    },
    dispatchGuardService: {
      // Default: every acquire wins. Tests for the dispatch-guard behaviour
      // override this to simulate the "already dispatched" branch.
      acquire: vi.fn().mockResolvedValue({ acquired: true, token: 'test-token' }),
      release: vi.fn().mockResolvedValue(undefined),
    },
    // Default: under the limit. Burst-limit route tests make `consume` reject.
    submitBurstLimiter: {
      consume: vi.fn().mockResolvedValue(undefined),
    },
    // Default: under the free chat limit. Chat route tests make `consume` reject.
    chatFairUse: {
      consume: vi.fn().mockResolvedValue(undefined),
    },
    idempotencyRepository: {
      findByHash: vi.fn().mockResolvedValue(null),
      reserveHash: vi.fn().mockResolvedValue({ created: true, doc: {} }),
      completeHash: vi.fn().mockResolvedValue(undefined),
      invalidateByHash: vi.fn().mockResolvedValue(undefined),
      invalidateStaleCompleted: vi.fn().mockResolvedValue(undefined),
      invalidateByVideoSummaryId: vi.fn().mockResolvedValue(undefined),
    },
    userRepository: {
      // Default: every authenticated test user is active. Override in
      // soft-delete tests by mocking findById to return `{ deletedAt: ... }`.
      findById: vi.fn().mockResolvedValue({
        _id: { toString: () => testUser.userId },
        email: testUser.email,
        deletedAt: null,
      }),
      findByEmail: vi.fn().mockResolvedValue(null),
      findByUsername: vi.fn().mockResolvedValue(null),
      create: vi.fn(),
      update: vi.fn().mockResolvedValue(undefined),
      updateLastLogin: vi.fn().mockResolvedValue(undefined),
      markSoftDeleted: vi.fn(),
      clearSoftDelete: vi.fn(),
      findExpiredSoftDeletes: vi.fn().mockResolvedValue([]),
      hardDelete: vi.fn().mockResolvedValue(true),
      // Default: every signed admin actor still holds the permission. Demotion
      // tests override it with false.
      hasPermission: vi.fn().mockResolvedValue(true),
      listAdmins: vi.fn().mockResolvedValue([]),
    },
    userDeletionRepository: {
      insert: vi.fn(),
      findByOriginalUserId: vi.fn().mockResolvedValue(null),
      findByEmailHash: vi.fn().mockResolvedValue(null),
    },
    userDeletionService: {
      requestDeletion: vi.fn(),
      cancelDeletion: vi.fn(),
      executeHardDelete: vi.fn(),
      runScheduledDeletions: vi.fn().mockResolvedValue({ processed: 0, succeeded: 0, failed: 0, failures: [] }),
    },
    videoCascadeService: {
      deleteVideo: vi.fn(),
    },
    auditService: {
      record: vi.fn().mockResolvedValue('audit-id'),
      recordBestEffort: vi.fn().mockResolvedValue('audit-id'),
      list: vi.fn().mockResolvedValue([]),
    },
    // Never reconcile roles against a real database from a route test.
    adminSeedService: {
      run: vi.fn().mockResolvedValue({ status: 'skipped_empty', promoted: 0, demoted: 0 }),
    },
    // Never touch the dev Redis from a route test: every signature is a first use.
    adminReplayGuard: {
      claim: vi.fn().mockResolvedValue(true),
    },
    alertConfigService: {
      replace: vi.fn().mockResolvedValue({ before: {}, after: {} }),
    },
  };
}

// Test user for authenticated requests
export const testUser = {
  userId: 'test-user-id',
  email: 'test@example.com',
};

/** A user id (24-char hex) for the admin that signed `/api/admin/*` requests act as. */
export const TEST_ADMIN_USER_ID = 'a1b2c3d4e5f60718293a4b5c';

export interface AdminActorRequest {
  headers: Record<string, string>;
  /** The exact serialized body the signature covers; absent for body-less requests. */
  payload?: string;
}

/**
 * Headers (and body) for an `/api/admin/*` request made the way vie-admin
 * makes it: the shared key plus a fresh `x-admin-actor` signed over the method,
 * the exact request target (`url`, path plus query, as it will be injected) and
 * the exact body bytes. Pass the result to `app.inject` together with the same
 * `method` and `url`.
 */
export function adminActorRequest(
  method: string,
  url: string,
  body?: Record<string, unknown>,
  options: { userId?: string; nowSeconds?: number } = {},
): AdminActorRequest {
  const { userId = TEST_ADMIN_USER_ID, nowSeconds = Math.floor(Date.now() / 1000) } = options;
  const payload = body === undefined ? undefined : JSON.stringify(body);
  const actor = signActorHeader(
    config.ADMIN_SESSION_SECRET,
    userId,
    { method, target: url, bodySha256: sha256Hex(payload ?? '') },
    nowSeconds,
  );
  return {
    headers: {
      'x-admin-key': config.ADMIN_API_KEY,
      'x-admin-actor': actor,
      ...(payload === undefined ? {} : { 'content-type': 'application/json' }),
    },
    ...(payload === undefined ? {} : { payload }),
  };
}

// Generate a valid JWT for testing
export async function getAuthHeader(app: FastifyInstance): Promise<string> {
  const token = app.jwt.sign({ userId: testUser.userId, email: testUser.email, type: 'access' });
  return `Bearer ${token}`;
}

// Build app with mock container for testing
export async function buildTestApp(mockContainer?: Partial<MockContainer>): Promise<FastifyInstance> {
  // Drop any cached soft-delete verdicts from prior specs in the same vitest
  // worker — otherwise a stale `false` entry would let a test that re-mocks
  // `findById` to return a deleted user slip through the JWT auth hook.
  softDeleteCache._resetForTests();

  const app = await buildApp({
    logger: false,
    // Pass container overrides to buildApp for proper injection
    container: mockContainer as Partial<typeof app.container>,
  });

  return app;
}
