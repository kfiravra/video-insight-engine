import { describe, it, expect, beforeAll, afterAll, beforeEach, vi } from 'vitest';
import { FastifyInstance } from 'fastify';
import { buildTestApp, createMockContainer, type MockContainer } from '../test/helpers.js';
import { ObjectId } from 'mongodb';

// Mock global fetch for summarizer service calls
const mockFetch = vi.fn();
global.fetch = mockFetch;

// pipeline-1min 2.1: a provisional tab (the plan-done Moments tab) streams
// before extraction and is replaced by id — or, under another id, by the tab
// whose `replaces` names it. Tabs are relay-only — the summarizer's save is
// their sole writer — so no stream event, provisional or final, may touch the
// stored output.
describe('stream routes — tab_ready relay (provisional → final)', () => {
  let app: FastifyInstance;
  let mockContainer: MockContainer;
  let authHeader: string;

  const validVideoSummaryId = new ObjectId().toHexString();
  // The stream route uses new ObjectId(req.user.userId), so userId MUST be a valid ObjectId
  const validUserObjectId = new ObjectId().toHexString();

  const provisionalTab = {
    event: 'tab_ready',
    id: 'key_moments',
    label: 'Key Moments',
    emoji: '🎬',
    component: 'moment_track',
    props: { items: [{ label: 'Outline section' }] },
    position: 3,
    final: false,
    provisional: true,
  };
  const finalTab = {
    event: 'tab_ready',
    id: 'key_moments',
    label: 'Key Moments',
    emoji: '🎬',
    component: 'moment_track',
    props: { items: [{ label: 'Extracted moment' }] },
    position: 4,
    final: true,
  };
  const replacingTab = {
    event: 'tab_ready',
    id: 'segments',
    label: 'Segments',
    emoji: '🎙️',
    component: 'moment_track',
    props: { items: [{ label: 'Deep dive' }] },
    position: 2,
    final: true,
    replaces: 'key_moments',
  };
  const completeEvent = { event: 'complete', tabCount: 1, tabIds: ['key_moments'], processingTimeMs: 1 };

  beforeAll(async () => {
    mockContainer = createMockContainer();
    app = await buildTestApp(mockContainer);
    await app.ready();
    const token = app.jwt.sign({ userId: validUserObjectId, email: 'test@example.com' });
    authHeader = `Bearer ${token}`;
  });

  afterAll(async () => {
    await app.close();
  });

  beforeEach(() => {
    vi.clearAllMocks();
    mockFetch.mockReset();
  });

  function mockSummarizerStream(events: Record<string, unknown>[]): void {
    const chunks = events.map((e) => new TextEncoder().encode(`data: ${JSON.stringify(e)}\n\n`));
    const read = vi.fn();
    for (const value of chunks) read.mockResolvedValueOnce({ done: false, value });
    read.mockResolvedValueOnce({ done: true, value: undefined });
    mockFetch.mockResolvedValue({
      ok: true,
      status: 200,
      body: { getReader: () => ({ read, cancel: vi.fn().mockResolvedValue(undefined) }) },
    });
  }

  async function relayedEvents(): Promise<unknown[]> {
    const response = await app.inject({
      method: 'GET',
      url: `/api/videos/${validVideoSummaryId}/stream`,
      headers: { authorization: authHeader, accept: 'text/event-stream' },
    });
    return response.body
      .split('\n\n')
      .filter((frame) => frame.startsWith('data: '))
      .map((frame) => JSON.parse(frame.slice('data: '.length)));
  }

  it('should forward the provisional tab, its final replacement and complete unchanged, in order', async () => {
    mockSummarizerStream([provisionalTab, finalTab, completeEvent]);

    const relayed = await relayedEvents();

    expect(relayed).toEqual([provisionalTab, finalTab, completeEvent]);
  });

  it('should forward replaces on the tab that takes the place of the provisional tab under another id', async () => {
    const replacedComplete = { ...completeEvent, tabIds: ['segments'] };
    mockSummarizerStream([provisionalTab, replacingTab, replacedComplete]);

    const relayed = await relayedEvents();

    expect(relayed).toEqual([provisionalTab, replacingTab, replacedComplete]);
  });

  it('should write nothing to the stored video while tabs stream', async () => {
    mockSummarizerStream([provisionalTab, finalTab, completeEvent]);
    const { updateCacheEntry, mergeSynthesis, promoteCompletedVersion, restoreServedVersion } =
      mockContainer.videoRepository;

    await relayedEvents();

    const writes = [updateCacheEntry, mergeSynthesis, promoteCompletedVersion, restoreServedVersion];
    expect(writes.filter((write) => write.mock.calls.length > 0)).toEqual([]);
  });
});
