import { Db } from 'mongodb';
import { FastifyBaseLogger } from 'fastify';
import { Redis } from 'ioredis';

// Repositories
import { VideoRepository } from './repositories/video.repository.js';
import { FolderRepository } from './repositories/folder.repository.js';
import { UserRepository } from './repositories/user.repository.js';
import { ShareRepository } from './repositories/share.repository.js';
import { UserCostRepository } from './repositories/user-cost.repository.js';
import { IdempotencyRepository } from './repositories/idempotency.repository.js';
import { UserDeletionRepository } from './repositories/user-deletion.repository.js';
import { VideoDeletionRepository } from './repositories/video-deletion.repository.js';
import { AdminAuditRepository } from './repositories/admin-audit.repository.js';
import { BillingRepository } from './repositories/billing.repository.js';
import { BillingShadowRepository } from './repositories/billing-shadow.repository.js';
import { LlmUsageRepository } from './repositories/llm-usage.repository.js';
import { LlmAlertsRepository } from './repositories/llm-alerts.repository.js';
import { BillingMaintenanceRepository } from './repositories/billing-maintenance.repository.js';

// Services
import { AuthService } from './services/auth.service.js';
import { VideoService } from './services/video.service.js';
import { FolderService } from './services/folder.service.js';
import { PlaylistService } from './services/playlist.service.js';
import { SummarizerClient } from './services/summarizer-client.js';
import { AssistantClient } from './services/assistant-client.js';
import { ShareService } from './services/share.service.js';
import { OgImageService } from './services/og-image.service.js';
import { CostMonitorService } from './services/cost-monitor.service.js';
import { QueuePublisher, type ChannelSupplier } from './services/queue-publisher.service.js';
import { IdempotencyService } from './services/idempotency.service.js';
import { DispatchGuardService, noOpDispatchGuard, type IDispatchGuard } from './services/dispatch-guard.service.js';
import { ChatFairUseService, noOpChatFairUse, type IChatFairUse } from './services/chat-fair-use.service.js';
import { UserDeletionService } from './services/user-deletion.service.js';
import { VideoCascadeService } from './services/video-cascade.service.js';
import { AuditService } from './services/audit.service.js';
import { AdminSeedService } from './services/admin-seed.service.js';
import { BillingService } from './services/billing/billing.service.js';
import { BillingGate } from './services/billing/billing-gate.js';
import { BillingShadowService } from './services/billing/billing-shadow.service.js';
import { ShadowRunner } from './services/billing/shadow-runner.js';
import { BillingRepairService } from './services/billing/billing-repair.service.js';
import { LedgerEnforcement } from './services/billing/ledger-enforcement.js';
import { RedisRepairLock, singleProcessRepairLock } from './services/billing/repair-lock.js';
import {
  SubmitBurstLimiter,
  noOpSubmitBurstLimiter,
  type ISubmitBurstLimiter,
} from './services/billing/burst-limit.js';
import { config } from './config.js';

export interface Container {
  // Repositories
  videoRepository: VideoRepository;
  folderRepository: FolderRepository;
  userRepository: UserRepository;
  shareRepository: ShareRepository;
  userCostRepository: UserCostRepository;
  idempotencyRepository: IdempotencyRepository;
  userDeletionRepository: UserDeletionRepository;
  videoDeletionRepository: VideoDeletionRepository;
  adminAuditRepository: AdminAuditRepository;
  billingRepository: BillingRepository;

  // Services
  authService: AuthService;
  videoService: VideoService;
  folderService: FolderService;
  playlistService: PlaylistService;
  summarizerClient: SummarizerClient;
  assistantClient: AssistantClient;
  shareService: ShareService;
  ogImageService: OgImageService;
  costMonitorService: CostMonitorService;
  queuePublisher: QueuePublisher;
  idempotencyService: IdempotencyService;
  dispatchGuardService: IDispatchGuard;
  submitBurstLimiter: ISubmitBurstLimiter;
  chatFairUse: IChatFairUse;
  userDeletionService: UserDeletionService;
  videoCascadeService: VideoCascadeService;
  auditService: AuditService;
  adminSeedService: AdminSeedService;
  billingService: BillingService;
  billingShadowService: BillingShadowService;
  billingRepairService: BillingRepairService;
  /** Present when BILLING_LEDGER_ENFORCE is on: the minutes ledger decides every submit. */
  billingEnforcement: LedgerEnforcement | null;
}

export interface CreateContainerOptions {
  /** Supplier for a confirm channel — wired up by the rabbitmq plugin. */
  queueChannelSupplier?: ChannelSupplier;
  /** Redis client — wired up by the redis plugin. Optional so tests can pass
   *  an in-memory stub instead of standing up a real Redis. */
  redisClient?: Redis;
}

export function createContainer(
  db: Db,
  logger: FastifyBaseLogger,
  options: CreateContainerOptions = {},
): Container {
  // Create repositories
  const videoRepository = new VideoRepository(db);
  const folderRepository = new FolderRepository(db);
  const userRepository = new UserRepository(db);
  const shareRepository = new ShareRepository(db);
  const userCostRepository = new UserCostRepository(db);
  const idempotencyRepository = new IdempotencyRepository(db);
  const userDeletionRepository = new UserDeletionRepository(db);
  const videoDeletionRepository = new VideoDeletionRepository(db);
  const adminAuditRepository = new AdminAuditRepository(db);
  const billingRepository = new BillingRepository(db);

  // Create external clients
  const summarizerClient = new SummarizerClient(logger, options.redisClient);
  const assistantClient = new AssistantClient(logger);

  // Queue publisher — falls back to a stub supplier in tests / pre-plugin paths.
  // The video service decides per-request whether to use HTTP or queue, so the
  // stub is fine when USE_QUEUE_PIPELINE=false.
  const channelSupplier: ChannelSupplier =
    options.queueChannelSupplier ??
    (() => {
      throw new Error('RabbitMQ channel supplier not configured');
    });
  const queuePublisher = new QueuePublisher(channelSupplier, logger);

  // Create services with injected dependencies.
  // Order matters: idempotencyService and dispatchGuardService are constructed
  // first because videoService depends on both — content-addressed dedup-key
  // computation and the Redis-backed publish guard, respectively.
  const idempotencyService = new IdempotencyService(idempotencyRepository, logger);
  // Tests that don't exercise dispatch can pass a stub Redis; production wires
  // in the real client via the redisPlugin. Without a Redis client we fall back
  // to the exported `noOpDispatchGuard`, which satisfies IDispatchGuard and
  // always reports acquired — same semantics as the fail-open Redis path.
  const dispatchGuardService: IDispatchGuard = options.redisClient
    ? new DispatchGuardService(options.redisClient, config.DISPATCH_GUARD_TTL_SECONDS, logger)
    : noOpDispatchGuard;
  // Same fail-open stance: no Redis client, no burst limit.
  const submitBurstLimiter: ISubmitBurstLimiter = options.redisClient
    ? new SubmitBurstLimiter(options.redisClient, config.SUBMIT_BURST_LIMIT_PER_HOUR, logger)
    : noOpSubmitBurstLimiter;
  const authService = new AuthService(userRepository, logger);
  const videoService = new VideoService(
    videoRepository,
    summarizerClient,
    queuePublisher,
    idempotencyService,
    dispatchGuardService,
    userRepository,
    logger,
  );
  const folderService = new FolderService(folderRepository, logger);
  const costMonitorService = new CostMonitorService(db, logger, userCostRepository);
  const billingService = new BillingService(billingRepository, userRepository, logger);
  // Chat fair use reads the account plan only; same fail-open stance as the burst limiter.
  const chatFairUse: IChatFairUse = options.redisClient
    ? new ChatFairUseService(options.redisClient, billingService, config.CHAT_FREE_MESSAGES_PER_DAY, logger)
    : noOpChatFairUse;
  const llmUsageRepository = new LlmUsageRepository(db);
  const billingGate = new BillingGate(billingService, summarizerClient);
  // P3.1: with enforcement on the ledger decides and there is no old decision to shadow.
  const billingEnforcement = config.BILLING_LEDGER_ENFORCE
    ? new LedgerEnforcement({ billing: billingService, gate: billingGate, llmUsage: llmUsageRepository, dispatchStamps: videoRepository, logger })
    : null;
  // Shadow ledger (P1.7): writes beside the old gate, never decides.
  const billingShadowService = new BillingShadowService({
    enabled: config.BILLING_SHADOW_ENABLED && !billingEnforcement,
    gate: billingGate,
    billing: billingService,
    rows: new BillingShadowRepository(db),
    llmUsage: llmUsageRepository,
    dispatchStamps: videoRepository,
    runner: new ShadowRunner(logger),
    logger,
  });
  const playlistService = new PlaylistService(
    videoService,
    folderService,
    summarizerClient,
    logger,
    costMonitorService,
    billingShadowService,
    billingEnforcement ?? undefined,
  );
  const shareService = new ShareService(shareRepository, videoRepository, logger);
  const ogImageService = new OgImageService(logger);
  const userDeletionService = new UserDeletionService(
    db,
    userRepository,
    userDeletionRepository,
    logger,
  );
  const videoCascadeService = new VideoCascadeService(
    db,
    videoRepository,
    idempotencyService,
    dispatchGuardService,
    summarizerClient,
    videoDeletionRepository,
    logger,
  );
  // Billing repair job (P1.6); the billing-repair plugin schedules it.
  const billingRepairService = new BillingRepairService({
    billing: billingService,
    maintenance: new BillingMaintenanceRepository(db),
    llmUsage: llmUsageRepository,
    alerts: new LlmAlertsRepository(db),
    deletions: userDeletionService,
    lock: options.redisClient
      ? new RedisRepairLock(options.redisClient, config.BILLING_REPAIR_INTERVAL_SECONDS, logger)
      : singleProcessRepairLock,
    logger,
    holdMaxAgeHours: config.BILLING_HOLD_MAX_AGE_HOURS,
    recurringLotGraceMs: 2 * config.BILLING_REPAIR_INTERVAL_SECONDS * 1000,
  });
  const auditService = new AuditService(adminAuditRepository, logger);
  const adminSeedService = new AdminSeedService(
    config.ADMIN_EMAILS,
    config.DEMO_USER_EMAIL,
    userRepository,
    auditService,
    logger,
  );

  return {
    // Repositories
    videoRepository,
    folderRepository,
    userRepository,
    shareRepository,
    userCostRepository,
    idempotencyRepository,
    userDeletionRepository,
    videoDeletionRepository,
    adminAuditRepository,
    billingRepository,

    // Services
    authService,
    videoService,
    folderService,
    playlistService,
    summarizerClient,
    assistantClient,
    shareService,
    ogImageService,
    costMonitorService,
    queuePublisher,
    idempotencyService,
    dispatchGuardService,
    submitBurstLimiter,
    chatFairUse,
    userDeletionService,
    videoCascadeService,
    auditService,
    adminSeedService,
    billingService,
    billingShadowService,
    billingRepairService,
    billingEnforcement,
  };
}

// Type declaration for Fastify
declare module 'fastify' {
  interface FastifyInstance {
    container: Container;
  }
}
