import { ObjectId } from 'mongodb';
import { vi } from 'vitest';
import { mockLogger } from '../../../test/setup.js';
import { IdempotencyRepository } from '../../../repositories/idempotency.repository.js';
import { VideoRepository } from '../../../repositories/video.repository.js';
import { IdempotencyService } from '../../idempotency.service.js';
import { PlaylistService } from '../../playlist.service.js';
import { VideoSubmissionService } from '../../video-submission.service.js';
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
import type { AccountKind, ReplayScenario, ReplayStep, SubmitOutcome } from './shadow-replay.scenarios.js';
import type { BillingShadowDocument } from '../../../repositories/billing-shadow.repository.js';

// Wiring for the P1.11 replay harness: the real VideoSubmissionService,
// PlaylistService and POST /internal/status, the real old dollar gate
// (OldGateRecorder) and the real shadow ledger, side by side on the test
// replica set. Only createVideo (dispatch) and the playlist extract are faked:
// their outcome is the scenario's input.

export interface RunResult {
  requestId: string;
  /** The live old gate's decision (P3.2 deletes the old gate, and this with it). */
  liveOld: BillingShadowDecision;
  /** Admitted / charged as the ledger entries show them. */
  ledger: BillingShadowDecision;
  row: BillingShadowDocument | null;
  /** What the submit threw, if anything (enforced mode reads the refusal reason from it). */
  error: unknown;
}

const logger = mockLogger as unknown as FastifyBaseLogger;
const ACCOUNT_CTX = () => ({ actorId: 'system:replay', reason: 'replay fixture', auditId: new ObjectId().toHexString() });

interface ScenarioState {
  scenario: string;
  userId: string;
  tier: 'free' | 'pro';
  /** run → its requestId (single submits; playlist items are found after the fact). */
  requestIds: Map<string, string>;
  youtubeIds: Map<string, string>;
  /** run → the videoSummaryId its dispatch stamped. */
  dispatched: Map<string, string>;
  errors: Map<string, unknown>;
  outcomes: Array<{ run: string; outcome: SubmitOutcome }>;
}

export class ReplayHarness {
  readonly shadow: ShadowHarness;
  readonly oldGate: OldGateRecorder;
  private readonly submissions: VideoSubmissionService;
  private readonly playlists: PlaylistService;
  /** The playlist extract is a fetch to the summarizer; stub global fetch with this. */
  readonly fetchStub = vi.fn();
  private readonly createVideo = vi.fn();
  private state: ScenarioState | null = null;
  private videoCounter = 0;

  constructor(private readonly db: Db) {
    this.shadow = makeShadowHarness(db, { dispatchStamps: new VideoRepository(db) });
    this.oldGate = new OldGateRecorder(db, logger);
    const videoService = { createVideo: this.createVideo } as unknown as VideoService;
    this.createVideo.mockImplementation((userId: string, url: string, options: { requestId?: string }) =>
      this.dispatch(userId, url, options.requestId));
    this.submissions = new VideoSubmissionService(
      videoService,
      this.oldGate.service,
      new IdempotencyService(new IdempotencyRepository(db), logger),
      new VideoRepository(db),
      logger,
      this.shadow.shadow,
    );
    const folders = { create: vi.fn().mockResolvedValue({ id: 'folder-replay', name: 'Replay' }), getById: vi.fn() };
    this.playlists = new PlaylistService(
      videoService,
      folders as unknown as FolderService,
      {} as unknown as SummarizerClient,
      logger,
      this.oldGate.service,
      this.shadow.shadow,
    );
  }

  /** The app's container takes these, so POST /internal/status reaches the same old gate and shadow. */
  get costMonitorService(): CostMonitorService {
    return this.oldGate.service;
  }

  async run(app: FastifyInstance, scenario: ReplayScenario): Promise<Map<string, RunResult>> {
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
      requestIds: new Map(), youtubeIds: new Map(), dispatched: new Map(), errors: new Map(), outcomes: [],
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
        return this.submit(step.run, step.minutes, step.outcome, step.bypassCache ?? false);
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

  private async submit(run: string, minutes: number, outcome: SubmitOutcome, bypassCache: boolean): Promise<void> {
    const state = this.requireState();
    const requestId = state.requestIds.get(run) ?? `replay-${new ObjectId().toHexString()}-${run}`;
    state.requestIds.set(run, requestId);
    const youtubeId = this.youtubeIdFor(run);
    this.shadow.metadata.getVideoMetadata.mockResolvedValue({ duration: minutes * 60 });
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

  /** The faked dispatch: answers with the scenario's outcome and stamps the cache row like dispatchPipeline. */
  private async dispatch(userId: string, url: string, requestId: string | undefined): Promise<unknown> {
    const state = this.requireState();
    const next = state.outcomes.shift();
    if (!next) throw new Error(`${state.scenario}: unexpected createVideo for ${url}`);
    if (requestId) state.requestIds.set(next.run, requestId);
    if (next.outcome === 'throws') throw new Error('dispatch failed');
    const videoSummaryId = new ObjectId();
    const youtubeId = this.youtubeIdFor(next.run);
    if (next.outcome === 'created') {
      await this.db.collection('videoSummaryCache').insertOne({ _id: videoSummaryId, youtubeId, userId, requestId, status: 'pending' });
      state.dispatched.set(next.run, videoSummaryId.toHexString());
    }
    const video = { id: new ObjectId().toHexString(), videoSummaryId: videoSummaryId.toHexString(), youtubeId, status: 'pending' };
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
      const requestId = state.requestIds.get(run) ?? (await this.requestIdByVideo(state.youtubeIds.get(run)));
      const entries = await this.db.collection('ledgerEntries').find({ 'source.ref': requestId }).toArray();
      const held = entries.some((e) => e.type === 'hold');
      results.set(run, {
        requestId,
        liveOld: this.oldGate.decisionFor(run),
        ledger: { admitted: held, charged: held && !entries.some((e) => e.type === 'release') },
        row: await this.shadow.rowOf(requestId),
        error: state.errors.get(run),
      });
    }
    return results;
  }

  /** Playlist items the old gate refused never reach createVideo; their row is found by video. */
  private async requestIdByVideo(youtubeId: string | undefined): Promise<string> {
    const row = await this.db.collection('billingShadow').findOne({ youtubeId });
    if (!row) throw new Error(`${this.requireState().scenario}: no shadow row for ${youtubeId}`);
    return String(row.requestId);
  }

  private requireState(): ScenarioState {
    if (!this.state) throw new Error('replay harness: no scenario is running');
    return this.state;
  }
}
