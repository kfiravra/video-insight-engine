import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { setApiKey } from './api';
import { auditApi, billingActions } from './billing-actions';

const USER = 'a'.repeat(24);
const GRANT = 'b'.repeat(24);

function stubFetch(body: unknown = {}) {
  const fetchMock = vi.fn().mockResolvedValue({ ok: true, status: 200, json: () => Promise.resolve(body) });
  vi.stubGlobal('fetch', fetchMock);
  return fetchMock;
}

function lastCall(fetchMock: ReturnType<typeof stubFetch>) {
  const [url, init] = fetchMock.mock.calls.at(-1) as [string, RequestInit & { headers: Record<string, string> }];
  return { url, init, body: init.body ? JSON.parse(String(init.body)) : undefined };
}

describe('billingActions', () => {
  beforeEach(() => {
    localStorage.clear();
    setApiKey('session-token');
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it('should POST the plan to the account plan route', async () => {
    const fetchMock = stubFetch();

    await billingActions.setPlan(USER, { planId: 'pro', reason: 'invoice' }, 'key-12345678');

    const { url, init, body } = lastCall(fetchMock);
    expect([url, init.method]).toEqual([`/billing/accounts/${USER}/plan`, 'POST']);
    expect(body).toEqual({ planId: 'pro', reason: 'invoice' });
  });

  it('should send the idempotency key and the session as headers', async () => {
    const fetchMock = stubFetch();

    await billingActions.setCaps(USER, { blocked: true, reason: 'abuse' }, 'key-12345678');

    const { init } = lastCall(fetchMock);
    expect(init.headers).toMatchObject({
      'Idempotency-Key': 'key-12345678',
      'Content-Type': 'application/json',
      Authorization: 'Bearer session-token',
    });
  });

  it('should leave the idempotency header out when no key is given', async () => {
    const fetchMock = stubFetch();

    await billingActions.adjust(USER, { bucket: 'plan', minutes: -5, reason: 'fix' });

    expect(lastCall(fetchMock).init.headers).not.toHaveProperty('Idempotency-Key');
  });

  it('should keep an explicit null in the caps body so the cap is cleared', async () => {
    const fetchMock = stubFetch();

    await billingActions.setCaps(USER, { maxConcurrent: null, reason: 'reset' });

    expect(lastCall(fetchMock).body).toEqual({ maxConcurrent: null, reason: 'reset' });
  });

  it('should POST an adjustment with its sign', async () => {
    const fetchMock = stubFetch();

    await billingActions.adjust(USER, { bucket: 'credit', minutes: -30, reason: 'refund' });

    const { url, body } = lastCall(fetchMock);
    expect(url).toBe(`/billing/accounts/${USER}/adjust`);
    expect(body).toEqual({ bucket: 'credit', minutes: -30, reason: 'refund' });
  });

  it('should POST a recurring grant to the grants route', async () => {
    const fetchMock = stubFetch();

    await billingActions.createRecurringGrant(USER, { minutes: 300, every: 'month', active: true, reason: 'demo' });

    const { url, body } = lastCall(fetchMock);
    expect(url).toBe(`/billing/accounts/${USER}/recurring-grants`);
    expect(body).toEqual({ minutes: 300, every: 'month', active: true, reason: 'demo' });
  });

  it('should DELETE a recurring grant with its reason in the body', async () => {
    const fetchMock = stubFetch();

    await billingActions.deleteRecurringGrant(USER, GRANT, { reason: 'stale' });

    const { url, init, body } = lastCall(fetchMock);
    expect([url, init.method]).toEqual([`/billing/accounts/${USER}/recurring-grants/${GRANT}`, 'DELETE']);
    expect(body).toEqual({ reason: 'stale' });
  });
});

describe('auditApi.list', () => {
  beforeEach(() => {
    localStorage.clear();
    setApiKey('session-token');
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it('should query the audit route with the filters and cursor', async () => {
    const fetchMock = stubFetch({ entries: [], nextBefore: null });

    await auditApi.list({ targetId: USER, actorId: 'admin-1', limit: 50, before: '2026-10-01T00:00:00.000Z' });

    const query = new URLSearchParams(lastCall(fetchMock).url.split('?')[1]);
    expect(Object.fromEntries(query)).toEqual({
      targetId: USER,
      actorId: 'admin-1',
      limit: '50',
      before: '2026-10-01T00:00:00.000Z',
    });
  });

  it('should send no query string without filters', async () => {
    const fetchMock = stubFetch({ entries: [], nextBefore: null });

    await auditApi.list();

    expect(lastCall(fetchMock).url).toBe('/audit');
  });
});
