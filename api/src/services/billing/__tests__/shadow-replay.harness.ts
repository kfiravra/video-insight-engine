import { ObjectId } from 'mongodb';
import { vi } from 'vitest';
import { mockLogger } from '../../../test/setup.js';
import { IdempotencyRepository } from '../../../repositories/idempotency.repository.js';
import { VideoRepository } from '../../../repositories/video.repository.js';
import { IdempotencyService } from '../../idempotency.service.js';
import { PlaylistService } from '../../playlist.service.js';
import { VideoSubmissionService } from '../../video-submission.service.js';
import { MetadataUnavailableError, RunNotAllowedError } from '../../../utils/errors.js';
import { insertUser, makeBillingHarness, makeEnforcement } from './billing-fixtures.js';
import type { Db } from 'mongodb';
import type { FastifyBaseLogger, FastifyInstance } from 'fastify';
import type { BillingShadowDecision } from '@vie/types';
import type { FolderService } from '../../folder.service.js';
import type { SummarizerClient } from '../../summarizer-client.js';
import type { VideoService } from '../../video.service.js';
import type { LedgerEnforcement } from '../ledger-enforcement.js';
import type { BillingHarness } from './billing-fixtures.js';
import type { GateSubmission } from '../billing-gate.js';
import type { AccountKind, ReplayScenario, ReplayStep, SubmitOutcome } from './shadow-replay.scenarios.js';

// Wiring for the P1.11 replay harness: the real VideoSubmissionService,
// PlaylistService and POST /internal/status over the real minutes ledger
// (LedgerEnforcement, deciding on the request path) on the test replica set.
// Only createVideo (dispatch) and the playlist extract are faked: their
// outcome is the scenario's input. The old dollar gate is gone (P3.2): each
// scenario's recorded `oldDecision` is the old side.

export interface RunResult {
  requestId: string;
  /** Admitted (a hold, or served free from cache / an in-flight run) / charged, as the ledger and the serve show them. */
  ledger: BillingShadowDecision;
  /** What the submit threw, if anything (the refusal reason is read from it). */
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
  readonly ledger: BillingHarness;
  private readonly submissions: VideoSubmissionService;
  private readonly playlists: PlaylistService;
  /** The playlist extract is a fetch to the summarizer; stub global fetch with this. */
  readonly fetchStub = vi.fn();
  private readonly createVideo = vi.fn();
  /** The app's container takes it too, so POST /internal/status settles on the same ledger. */
  readonly enforcement: LedgerEnforcement;
  private state: ScenarioState | null = null;
  private videoCounter = 0;
  private indexed = false;

  constructor(private readonly db: Db) {
    this.ledger = makeBillingHarness(db);
    this.enforcement = makeEnforcement(db, this.ledger, new VideoRepository(db));
    this.recordRunIds();
    const videoService = { createVideo: this.createVideo } as unknown as VideoService;
    this.createVideo.mockImplementation((userId: string, url: string, options: DispatchOptions) =>
      this.dispatch(userId, url, options));
    this.submissions = new VideoSubmissionService(
      videoService,
      new IdempotencyService(new IdempotencyRepository(db), logger),
      new VideoRepository(db),
      logger,
      this.enforcement,
    );
    const folders = {
      create: vi.fn().mockResolvedValue({ id: 'folder-replay', name: 'Replay' }), getById: vi.fn(), delete: vi.fn().mockResolvedValue(undefined),
    };
    this.playlists = new PlaylistService(
      videoService,
      folders as unknown as FolderService,
      {} as unknown as SummarizerClient,
      this.enforcement,
      logger,
    );
  }

  /** Every quote names its run: learn the server run ids per video. */
  private recordRunIds(): void {
    const quote = this.ledger.gate.quote.bind(this.ledger.gate);
    this.ledger.gate.quote = async (submission: GateSubmission) => {
      const runs = this.state?.runIds.get(submission.youtubeId) ?? [];
      if (!runs.includes(submission.requestId)) runs.push(submission.requestId);
      this.state?.runIds.set(submission.youtubeId, runs);
      return quote(submission);
    };
  }

  async run(app: FastifyInstance, scenario: ReplayScenario): Promise<Map<string, RunResult>> {
    if (!this.indexed) {
      // An HTTP retry must meet the idempotency gate's unique key, as in prod.
      await this.db.collection('idempotencyKeys').createIndex({ hash: 1 }, { unique: true });
      this.indexed = true;
    }
    this.state = await this.openScenario(scenario);
    for (const step of scenario.steps) await this.runStep(app, step);
    return this.collect(scenario);
  }

  private async openScenario(scenario: ReplayScenario): Promise<ScenarioState> {
    const userId = await insertUser(this.db);
    await this.prepareAccount(scenario.account, userId);
    return {
      scenario: scenario.name, userId,
      requestIds: new Map(), runIds: new Map(), youtubeIds: new Map(), dispatched: new Map(), errors: new Map(), outcomes: [],
      servedFree: new Set(),
    };
  }

  /** The ledger side of the account; the old gate's day spend lives only in the recorded `oldDecision`. */
  private async prepareAccount(account: AccountKind, userId: string): Promise<void> {
    const { billing } = this.ledger;
    if (account === 'credit' || account === 'over_limit_credit' || account === 'near_limit_credit') {
      await billing.grantLot(userId, { minutes: 300 }, ACCOUNT_CTX());
    }
    if (account === 'pro') await billing.setPlan(userId, { planId: 'pro' }, ACCOUNT_CTX());
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
    if (lengthUnknown) this.ledger.metadata.getVideoMetadata.mockRejectedValue(new MetadataUnavailableError('timeout'));
    else this.ledger.metadata.getVideoMetadata.mockResolvedValue({ duration: minutes * 60 });
    state.outcomes = [{ run, outcome }];
    await this.submissions
      .submit({ userId: state.userId, url: `https://www.youtube.com/watch?v=${youtubeId}`, requestId, bypassCache })
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
    state.outcomes = items.map(({ run, outcome }) => ({ run, outcome }));
    await this.playlists.import(state.userId, 'https://www.youtube.com/playlist?list=PLreplay', undefined, 100);
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
      await this.db.collection('llm_usage').insertOne({
        request_id: requestId, video_summary_id: videoSummaryId, user_id: state.userId, cost_usd: costUsd, timestamp: new Date(),
      });
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
    await this.ledger.gate.admit({ requestId, userId: state.userId, youtubeId: this.youtubeIdFor('ledger-free'), durationSec: 300 });
    await this.ledger.billing.settle(requestId);
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
        ledger: { ...ledger, admitted: ledger.admitted || state.servedFree.has(run) },
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
