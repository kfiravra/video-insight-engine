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

// Services
import { AuthService } from './services/auth.service.js';
import { VideoService } from './services/video.service.js';
import { FolderService } from './services/folder.service.js';
import { PlaylistService } from './services/playlist.service.js';
import { SummarizerClient } from './services/summarizer-client.js';
import { AssistantClient } from './services/assistant-client.js';
import { ShareService } from './services/share.service.js';
import { OgImageService } from './services/og-image.service.js';
import { PaymentService } from './services/payment.service.js';
import { CostMonitorService } from './services/cost-monitor.service.js';
import { QueuePublisher, type ChannelSupplier } from './services/queue-publisher.service.js';
import { IdempotencyService } from './services/idempotency.service.js';
import { DispatchGuardService, noOpDispatchGuard, type IDispatchGuard } from './services/dispatch-guard.service.js';
import { UserDeletionService } from './services/user-deletion.service.js';
import { VideoCascadeService } from './services/video-cascade.service.js';
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

  // Services
  authService: AuthService;
  videoService: VideoService;
  folderService: FolderService;
  playlistService: PlaylistService;
  summarizerClient: SummarizerClient;
  assistantClient: AssistantClient;
  shareService: ShareService;
  ogImageService: OgImageService;
  paymentService: PaymentService;
  costMonitorService: CostMonitorService;
  queuePublisher: QueuePublisher;
  idempotencyService: IdempotencyService;
  dispatchGuardService: IDispatchGuard;
  userDeletionService: UserDeletionService;
  videoCascadeService: VideoCascadeService;
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

  // Create external clients
  const summarizerClient = new SummarizerClient(logger);
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
  const authService = new AuthService(userRepository, logger);
  const videoService = new VideoService(
    videoRepository,
    summarizerClient,
    queuePublisher,
    idempotencyService,
    dispatchGuardService,
    logger,
  );
  const folderService = new FolderService(folderRepository, logger);
  const costMonitorService = new CostMonitorService(db, logger, userCostRepository);
  const playlistService = new PlaylistService(videoService, folderService, summarizerClient, logger, costMonitorService);
  const shareService = new ShareService(shareRepository, videoRepository, logger);
  const ogImageService = new OgImageService(logger);
  const paymentService = new PaymentService(userRepository, logger);
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

    // Services
    authService,
    videoService,
    folderService,
    playlistService,
    summarizerClient,
    assistantClient,
    shareService,
    ogImageService,
    paymentService,
    costMonitorService,
    queuePublisher,
    idempotencyService,
    dispatchGuardService,
    userDeletionService,
    videoCascadeService,
  };
}

// Type declaration for Fastify
declare module 'fastify' {
  interface FastifyInstance {
    container: Container;
  }
}
