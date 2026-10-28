import { describe, it, expect, beforeAll, afterAll, vi } from 'vitest';
import type { FastifyInstance } from 'fastify';
import { buildTestApp, createMockContainer, type MockContainer } from '../test/helpers.js';
import { config } from '../config.js';

const PLAYLIST_URL = 'https://www.youtube.com/playlist?list=PLtest123';

describe('POST /api/playlists/import rate limit', () => {
  let app: FastifyInstance;
  let mockContainer: MockContainer;
  let headers: Record<string, string>;

  beforeAll(async () => {
    mockContainer = createMockContainer();
    app = await buildTestApp(mockContainer);
    await app.ready();
    const token = app.jwt.sign({ userId: 'cccccccccccccccccccccccc', email: 'import@example.com', type: 'access' });
    headers = { authorization: `Bearer ${token}`, 'content-type': 'application/json' };
    mockContainer.playlistService.import.mockResolvedValue({ playlistId: 'PLtest123', importedCount: 3 });
  });

  afterAll(async () => {
    await app.close();
  });

  async function importUntilLimited(): Promise<Awaited<ReturnType<FastifyInstance['inject']>>> {
    let last = await app.inject({ method: 'POST', url: '/api/playlists/import', headers, payload: { url: PLAYLIST_URL } });
    for (let sent = 1; sent <= config.RATE_LIMITS.PLAYLIST_IMPORT; sent += 1) {
      last = await app.inject({ method: 'POST', url: '/api/playlists/import', headers, payload: { url: PLAYLIST_URL } });
    }
    return last;
  }

  it('should answer RATE_LIMITED 429 once the import allowance is used', async () => {
    const response = await importUntilLimited();

    expect(response.statusCode).toBe(429);
    expect(response.json().error).toBe('RATE_LIMITED');
  });

  it('should never say daily or tomorrow, whatever the window', async () => {
    const response = await importUntilLimited();

    expect(response.json().message).not.toMatch(/daily|tomorrow|24/i);
  });

  it('should send the seconds left in the 24 hour window', async () => {
    const response = await importUntilLimited();

    expect(response.json().retryAfterSec).toBeGreaterThan(23 * 60 * 60);
  });

  it('should not run the import once limited', async () => {
    vi.mocked(mockContainer.playlistService.import).mockClear();

    await importUntilLimited();

    expect(mockContainer.playlistService.import).not.toHaveBeenCalled();
  });
});
