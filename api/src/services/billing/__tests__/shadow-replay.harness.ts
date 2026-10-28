import { ObjectId } from 'mongodb';
import { vi } from 'vitest';
import { mockLogger } from '../../../test/setup.js';
import { IdempotencyRepository } from '../../../repositories/idempotency.repository.js';
import { VideoRepository } from '../../../repositories/video.repository.js';
import { IdempotencyService } from '../../idempotency.service.js';
import { PlaylistService } from '../../playlist.service.js';
import { VideoSubmissionService } from '../../video-submission.service.js';
import { LedgerEnforcement } from '../ledger-enforcement.js';
import { LlmUsageRepository } from '../../../repositories/llm-usage.repository.js';
import { MetadataUnavailableError, RunNotAllowedError } from '../../../utils/errors.js';
import { insertUser, makeShadowHarness } from './billing-fixtures.js';
import { OldGateRecorder } from './shadow-replay.old-gate.js';
import type { Db } from 'mongodb';
import type { FastifyBaseLogger, FastifyInstance } from 'fastify';
import type { BillingShadowDecision } from '@vie/types';
import type { CostMonitorService } from '../../cost-monitor.service.js';
import type { FolderService } from '../../folder.service.js';
import type { SummarizerClient } from '../../summarizer-client.js';
import type { VideoService } from '../../video.service.js';
import type { ShadowHarness } from './billing-fixtures.js';
import type { GateSubmission } from '../billing-gate.js';
import type { AccountKind, ReplayScenario, ReplayStep, SubmitOutcome } from './shadow-replay.scenarios.js';
import type { BillingShadowDocument } from '../../../repositories/billing-shadow.repository.js';

// Wiring for the P1.11 replay harness: the real VideoSubmissionService,
// PlaylistService and POST /internal/status, the real old dollar gate
// (OldGateRecorder) and the real shadow ledger, side by side on the test
// replica set. Only createVideo (dispatch) and the playlist extract are faked:
// their outcome is the scenario's input. `enforced` (P3.1) hands the same
// services a LedgerEnforcement, so the ledger decides on the request path.

/** `shadow`: the old gate decides, the ledger shadows. `enforced`: BILLING_LEDGER_ENFORCE on. */
export type HarnessMode = 'shadow' | 'enforced';

export interface RunResult {
  requestId: string;
  /** The live old gate's decision (P3.2 deletes the old gate, and this with it). */
  liveOld: BillingShadowDecision;
  /** Admitted (a hold, or served free from cache / an in-flight run) / charged, as the ledger and the serve show them. */
  ledger: BillingShadowDecision;
  row: BillingShadowDocument | null;
  /** What the submit threw, if anything (enforced mode reads the refusal reason from it). */
  error: unknown;
  /** How many runs of this video took a hold (an HTTP retry must not add one). */
  heldRuns: number;
}

const logger = mockLogger as unknown as FastifyBaseLogger;

interface DispatchOptions {
  requestId?: string;
  billingRunId?: string;
  serveOnly?: boolean;
}
const ACCOUNT_CTX = () => ({ actorId: 'system:replay', reason: 'replay fixture', auditId: new ObjectId().toHexString() });

interface ScenarioState {
  scenario: string;
  userId: string;
  tier: 'free' | 'pro';
  /** run → the request id its dispatch carried (the llm_usage key). */
  requestIds: Map<string, string>;
  /** youtubeId → the ledger run ids (server-generated, P3.1) submitted for it, in order. */
  runIds: Map<string, string[]>;
  youtubeIds: Map<string, string>;
  /** run → the videoSummaryId its dispatch stamped. */
  dispatched: Map<string, string>;
  errors: Map<string, unknown>;
  outcomes: Array<{ run: string; outcome: SubmitOutcome }>;
  /** Runs answered from cache or by attaching (no run of their own). */
  servedFree: Set<string>;
}

export class ReplayHarness {
  readonly shadow: ShadowHarness;
  readonly oldGate: OldGateRecorder;
  private readonly submissions: VideoSubmissionService;
  private readonly playlists: PlaylistService;
  /** The playlist extract is a fetch to the summarizer; stub global fetch with this. */
  readonly fetchStub = vi.fn();
  private readonly createVideo = vi.fn();
  /** Present in `enforced` mode; the app's container takes it too. */
  readonly enforcement: LedgerEnforcement | null;
  private state: ScenarioState | null = null;
  private videoCounter = 0;
  private indexed = false;

  constructor(private readonly db: Db, mode: HarnessMode = 'shadow') {
    this.shadow = makeShadowHarness(db, { dispatchStamps: new VideoRepository(db) });
    this.oldGate = new OldGateRecorder(db, logger);
    this.enforcement = mode === 'enforced'
      ? new LedgerEnforcement({
        billing: this.shadow.billing, gate: this.shadow.gate, llmUsage: new LlmUsageRepository(db),
        dispatchStamps: new VideoRepository(db), logger,
      })
      : null;
    this.recordRunIds();
    const videoService = { createVideo: this.createVideo } as unknown as VideoService;
    this.createVideo.mockImplementation((userId: string, url: string, options: DispatchOptions) =>
      this.dispatch(userId, url, options));
    this.submissions = new VideoSubmissionService(
      videoService,
      this.oldGate.service,
      new IdempotencyService(new IdempotencyRepository(db), logger),
      new VideoRepository(db),
      logger,
      this.shadow.shadow,
      this.enforcement ?? undefined,
    );
    const folders = { create: vi.fn().mockResolvedValue({ id: 'folder-replay', name: 'Replay' }), getById: vi.fn() };
    this.playlists = new PlaylistService(
      videoService,
      folders as unknown as FolderService,
      {} as unknown as SummarizerClient,
      logger,
      this.oldGate.service,
      this.shadow.shadow,
      this.enforcement ?? undefined,
    );
  }

  /** Every quote names its run (shadow and enforcement both quote first): learn the server run ids per video. */
  private recordRunIds(): void {
    const quote = this.shadow.gate.quote.bind(this.shadow.gate);
    this.shadow.gate.quote = async (submission: GateSubmission) => {
      const runs = this.state?.runIds.get(submission.youtubeId) ?? [];
      if (!runs.includes(submission.requestId)) runs.push(submission.requestId);
      this.state?.runIds.set(submission.youtubeId, runs);
      return quote(submission);
    };
  }

  /** The app's container takes these, so POST /internal/status reaches the same old gate and shadow. */
  get costMonitorService(): CostMonitorService {
    return this.oldGate.service;
  }

  async run(app: FastifyInstance, scenario: ReplayScenario): Promise<Map<string, RunResult>> {
    if (!this.indexed) {
      // An HTTP retry must meet the idempotency gate's unique key, as in prod.
      await this.db.collection('idempotencyKeys').createIndex({ hash: 1 }, { unique: true });
      this.indexed = true;
    }
    this.oldGate.reset();
    this.state = await this.openScenario(scenario);
    for (const step of scenario.steps) {
      await this.runStep(app, step);
      await this.shadow.shadow.drain();
    }
    return this.collect(scenario);
  }

  private async openScenario(scenario: ReplayScenario): Promise<ScenarioState> {
    const userId = await insertUser(this.db);
    await this.prepareAccount(scenario.account, userId);
    return {
      scenario: scenario.name, userId, tier: scenario.account === 'pro' ? 'pro' : 'free',
      requestIds: new Map(), runIds: new Map(), youtubeIds: new Map(), dispatched: new Map(), errors: new Map(), outcomes: [],
      servedFree: new Set(),
    };
  }

  private async prepareAccount(account: AccountKind, userId: string): Promise<void> {
    const { billing } = this.shadow;
    if (account === 'credit' || account === 'over_limit_credit' || account === 'near_limit_credit') {
      await billing.grantLot(userId, { minutes: 300 }, ACCOUNT_CTX());
    }
    if (account === 'pro') await billing.setPlan(userId, { planId: 'pro' }, ACCOUNT_CTX());
    if (account === 'over_limit' || account === 'over_limit_credit') await this.oldGate.seedDaySpend(userId, 2);
    if (account === 'near_limit_credit') await this.oldGate.seedDaySpend(userId, 1.8);
  }

  private async runStep(app: FastifyInstance, step: ReplayStep): Promise<void> {
    switch (step.kind) {
      case 'submit':
        return this.submit(step.run, step.minutes, step.outcome, step.bypassCache ?? false, step.lengthUnknown ?? false);
      case 'playlist':
        return this.importPlaylist(step.items);
      case 'finish':
        return this.finish(app, step.run, step.status, step.costUsd);
      case 'ledger_free_video':
        return this.spendFreeVideoInLedger();
    }
  }

  private youtubeIdFor(run: string): string {
    const state = this.requireState();
    const known = state.youtubeIds.get(run);
    if (known) return known;
    const id = `rp${String(++this.videoCounter).padStart(9, '0')}`;
    state.youtubeIds.set(run, id);
    return id;
  }

  private async submit(run: string, minutes: number, outcome: SubmitOutcome, bypassCache: boolean, lengthUnknown: boolean): Promise<void> {
    const state = this.requireState();
    const requestId = state.requestIds.get(run) ?? `replay-${new ObjectId().toHexString()}-${run}`;
    state.requestIds.set(run, requestId);
    const youtubeId = this.youtubeIdFor(run);
    if (lengthUnknown) this.shadow.metadata.getVideoMetadata.mockRejectedValue(new MetadataUnavailableError('timeout'));
    else this.shadow.metadata.getVideoMetadata.mockResolvedValue({ duration: minutes * 60 });
    this.oldGate.expectRuns([run]);
    state.outcomes = [{ run, outcome }];
    await this.submissions
      .submit({ userId: state.userId, url: `https://www.youtube.com/watch?v=${youtubeId}`, tier: state.tier, requestId, bypassCache })
      .catch((err: unknown) => state.errors.set(run, err));
  }

  private async importPlaylist(items: Array<{ run: string; minutes: number; outcome: SubmitOutcome }>): Promise<void> {
    const state = this.requireState();
    const videos = items.map((item, i) => ({
      video_id: this.youtubeIdFor(item.run), title: item.run, position: i + 1, duration: item.minutes * 60, thumbnail_url: null,
    }));
    this.fetchStub.mockResolvedValue({
      ok: true,
      json: () => Promise.resolve({ playlist_id: 'PLreplay', title: 'Replay', channel: null, thumbnail_url: null, total_videos: videos.length, videos }),
    });
    this.oldGate.expectRuns(items.map((item) => item.run));
    state.outcomes = items.map(({ run, outcome }) => ({ run, outcome }));
    await this.playlists.import(state.userId, 'https://www.youtube.com/playlist?list=PLreplay', undefined, 100, undefined, state.tier);
  }

  /**
   * The faked dispatch: answers with the scenario's outcome, stamps the cache
   * row like dispatchPipeline (request id + billing run id) and files the
   * library row, so a repeated submit meets the idempotency gate's duplicate.
   */
  private async dispatch(userId: string, url: string, options: DispatchOptions): Promise<unknown> {
    const state = this.requireState();
    const next = state.outcomes[0];
    if (!next) throw new Error(`${state.scenario}: unexpected createVideo for ${url}`);
    // Like the real createVideo: serve-only never starts a run (and leaves the outcome for nobody).
    if (options.serveOnly && (next.outcome === 'created' || next.outcome === 'throws')) {
      state.outcomes.shift();
      throw new RunNotAllowedError();
    }
    state.outcomes.shift();
    const { requestId, billingRunId } = options;
    if (requestId) state.requestIds.set(next.run, requestId);
    if (next.outcome === 'throws') throw new Error('dispatch failed');
    if (next.outcome === 'cached' || next.outcome === 'attached') state.servedFree.add(next.run);
    const videoSummaryId = new ObjectId();
    const youtubeId = this.youtubeIdFor(next.run);
    if (next.outcome === 'created') {
      await this.db.collection('videoSummaryCache').insertOne({ _id: videoSummaryId, youtubeId, userId, requestId, billingRunId, status: 'pending' });
      state.dispatched.set(next.run, videoSummaryId.toHexString());
    }
    const userVideoId = new ObjectId();
    await this.db.collection('userVideos').insertOne({ _id: userVideoId, userId: new ObjectId(userId), videoSummaryId, youtubeId, status: 'pending' });
    const video = { id: userVideoId.toHexString(), videoSummaryId: videoSummaryId.toHexString(), youtubeId, status: 'pending' };
    return { video, cached: next.outcome === 'cached', ...(next.outcome === 'attached' && { attached: true }) };
  }

  /** The pipeline spends, then the summarizer relays the terminal status through the real route. */
  private async finish(app: FastifyInstance, run: string, status: 'completed' | 'failed', costUsd: number): Promise<void> {
    const state = this.requireState();
    const videoSummaryId = state.dispatched.get(run);
    const requestId = state.requestIds.get(run);
    if (!videoSummaryId || !requestId) throw new Error(`${state.scenario}: run ${run} was never dispatched`);
    if (costUsd > 0) {
      await this.db.collection('llm_usage').insertOne({ request_id: requestId, user_id: state.userId, cost_usd: costUsd, timestamp: new Date() });
    }
    const response = await app.inject({
      method: 'POST',
      url: '/internal/status',
      headers: { 'content-type': 'application/json', 'x-internal-secret': 'dev-internal-secret-change-me' },
      payload: { type: 'video.status', payload: { videoSummaryId, userId: state.userId, status } },
    });
    if (response.statusCode !== 200) throw new Error(`${state.scenario}: status relay answered ${response.statusCode}`);
  }

  private async spendFreeVideoInLedger(): Promise<void> {
    const state = this.requireState();
    const requestId = `replay-ledger-free-${new ObjectId().toHexString()}`;
    await this.shadow.gate.admit({ requestId, userId: state.userId, youtubeId: this.youtubeIdFor('ledger-free'), durationSec: 300 });
    await this.shadow.billing.settle(requestId);
  }

  private async collect(scenario: ReplayScenario): Promise<Map<string, RunResult>> {
    const state = this.requireState();
    const results = new Map<string, RunResult>();
    for (const { run } of scenario.expect) {
      const youtubeId = state.youtubeIds.get(run) ?? '';
      const runIds = state.runIds.get(youtubeId) ?? [];
      const requestId = runIds[runIds.length - 1];
      if (!requestId) throw new Error(`${scenario.name}: run ${run} was never quoted`);
      const ledger = await this.ledgerDecision(requestId);
      results.set(run, {
        requestId,
        liveOld: this.oldGate.decisionFor(run),
        ledger: { ...ledger, admitted: ledger.admitted || state.servedFree.has(run) },
        row: await this.shadow.rowOf(requestId),
        error: state.errors.get(run),
        heldRuns: await this.heldRuns(runIds),
      });
    }
    return results;
  }

  private async ledgerDecision(runId: string): Promise<BillingShadowDecision> {
    const entries = await this.db.collection('ledgerEntries').find({ 'source.ref': runId }).toArray();
    const held = entries.some((e) => e.type === 'hold');
    return { admitted: held, charged: held && !entries.some((e) => e.type === 'release') };
  }

  private async heldRuns(runIds: string[]): Promise<number> {
    const held = await this.db.collection('ledgerEntries').distinct('source.ref', { type: 'hold', 'source.ref': { $in: runIds } });
    return held.length;
  }

  private requireState(): ScenarioState {
    if (!this.state) throw new Error('replay harness: no scenario is running');
    return this.state;
  }
}
