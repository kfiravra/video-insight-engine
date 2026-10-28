import { describe, it, expect } from 'vitest';
import {
  buildAdjustRequest,
  buildCapsRequest,
  buildDeleteGrantRequest,
  buildPlanRequest,
  buildRecurringGrantRequest,
  capsToForm,
  MAX_ADMIN_MINUTES,
  MAX_REASON_LENGTH,
} from './billing-forms';

const NOW = new Date('2026-10-28T12:00:00.000Z');

describe('buildPlanRequest', () => {
  it('should require a reason', () => {
    expect(() => buildPlanRequest({ planId: 'pro', periodEnd: '', reason: '   ' })).toThrow('Reason is required');
  });

  it('should trim the reason', () => {
    expect(buildPlanRequest({ planId: 'pro', periodEnd: '', reason: '  paid by invoice ' })).toEqual({
      planId: 'pro',
      reason: 'paid by invoice',
    });
  });

  it('should reject a reason over the limit', () => {
    const reason = 'x'.repeat(MAX_REASON_LENGTH + 1);

    expect(() => buildPlanRequest({ planId: 'pro', periodEnd: '', reason })).toThrow(/at most 500/);
  });

  it('should send the end of the chosen day in UTC as the term end', () => {
    const request = buildPlanRequest({ planId: 'pro', periodEnd: '2027-03-01', reason: 'r' }, NOW);

    expect(request).toEqual({ planId: 'pro', periodEnd: '2027-03-01T23:59:59.000Z', reason: 'r' });
  });

  it('should reject a term end in the past', () => {
    expect(() => buildPlanRequest({ planId: 'pro', periodEnd: '2026-10-01', reason: 'r' }, NOW)).toThrow(
      'Term end must be in the future',
    );
  });

  it('should not send a term end for the free plan', () => {
    const request = buildPlanRequest({ planId: 'free', periodEnd: '2027-03-01', reason: 'r' }, NOW);

    expect(request).toEqual({ planId: 'free', reason: 'r' });
  });
});

describe('buildCapsRequest', () => {
  const none = capsToForm(undefined);
  const form = (overrides: Partial<typeof none> & { reason?: string }) => ({ ...none, reason: 'why', ...overrides });

  it('should require a reason', () => {
    expect(() => buildCapsRequest(form({ maxConcurrent: '2', reason: '' }), none)).toThrow('Reason is required');
  });

  it('should require at least one change', () => {
    expect(() => buildCapsRequest(form({}), none)).toThrow('Change at least one cap');
  });

  it('should send only the caps that changed', () => {
    const request = buildCapsRequest(form({ maxVideoMinutes: '60' }), none);

    expect(request).toEqual({ maxVideoMinutes: 60, reason: 'why' });
  });

  it('should clear a cap that was emptied', () => {
    const initial = capsToForm({ maxConcurrent: 3, maxVideoMinutes: 90 });

    const request = buildCapsRequest({ ...initial, maxConcurrent: '', reason: 'why' }, initial);

    expect(request).toEqual({ maxConcurrent: null, reason: 'why' });
  });

  it('should leave a cap alone when its value is unchanged', () => {
    const initial = capsToForm({ maxConcurrent: 3 });

    const request = buildCapsRequest({ ...initial, maxVideoMinutes: '30', reason: 'why' }, initial);

    expect(request).toEqual({ maxVideoMinutes: 30, reason: 'why' });
  });

  it('should block the account when the box is ticked', () => {
    expect(buildCapsRequest(form({ blocked: true }), none)).toEqual({ blocked: true, reason: 'why' });
  });

  it('should clear the block, not store false, when the box is unticked', () => {
    const initial = capsToForm({ blocked: true });

    const request = buildCapsRequest({ ...initial, blocked: false, reason: 'why' }, initial);

    expect(request).toEqual({ blocked: null, reason: 'why' });
  });

  it('should allow zero free videos per day', () => {
    expect(buildCapsRequest(form({ freeVideosPerDay: '0' }), none)).toEqual({ freeVideosPerDay: 0, reason: 'why' });
  });

  it.each([
    ['maxVideoMinutes', '2', 'Max video length must be between 3 and 180'],
    ['maxVideoMinutes', '181', 'Max video length must be between 3 and 180'],
    ['freeVideosPerDay', '101', 'Free videos per day must be between 0 and 100'],
    ['maxConcurrent', '0', 'Runs at once must be between 1 and 1000'],
    ['maxConcurrent', '1.5', 'Runs at once must be a whole number'],
  ] as const)('should reject %s = %s', (field, value, message) => {
    expect(() => buildCapsRequest(form({ [field]: value }), none)).toThrow(message);
  });
});

describe('capsToForm', () => {
  it('should show the current caps as strings', () => {
    expect(capsToForm({ maxVideoMinutes: 60, freeVideosPerDay: 0, maxConcurrent: null, blocked: true })).toEqual({
      maxVideoMinutes: '60',
      freeVideosPerDay: '0',
      maxConcurrent: '',
      blocked: true,
    });
  });
});

describe('buildAdjustRequest', () => {
  it('should keep the sign of the minutes', () => {
    expect(buildAdjustRequest({ bucket: 'credit', minutes: '-30', reason: 'refund' })).toEqual({
      bucket: 'credit',
      minutes: -30,
      reason: 'refund',
    });
  });

  it('should reject 0 minutes', () => {
    expect(() => buildAdjustRequest({ bucket: 'plan', minutes: '0', reason: 'r' })).toThrow('Minutes must not be 0');
  });

  it.each(['', '1.5', 'abc'])('should reject "%s" minutes', (minutes) => {
    expect(() => buildAdjustRequest({ bucket: 'plan', minutes, reason: 'r' })).toThrow('Minutes must be a whole number');
  });

  it.each([`${MAX_ADMIN_MINUTES + 1}`, `-${MAX_ADMIN_MINUTES + 1}`])('should reject %s minutes', (minutes) => {
    expect(() => buildAdjustRequest({ bucket: 'plan', minutes, reason: 'r' })).toThrow(/Minutes must be between/);
  });

  it('should require a reason', () => {
    expect(() => buildAdjustRequest({ bucket: 'plan', minutes: '5', reason: '' })).toThrow('Reason is required');
  });
});

describe('buildRecurringGrantRequest', () => {
  it('should build a monthly grant', () => {
    expect(buildRecurringGrantRequest({ minutes: '300', active: true, reason: 'demo' })).toEqual({
      minutes: 300,
      every: 'month',
      active: true,
      reason: 'demo',
    });
  });

  it('should keep an inactive grant inactive', () => {
    expect(buildRecurringGrantRequest({ minutes: '300', active: false, reason: 'demo' }).active).toBe(false);
  });

  it.each(['0', '-5', '100001'])('should reject %s minutes', (minutes) => {
    expect(() => buildRecurringGrantRequest({ minutes, active: true, reason: 'demo' })).toThrow(
      /Minutes must be between 1 and/,
    );
  });

  it('should require a reason', () => {
    expect(() => buildRecurringGrantRequest({ minutes: '5', active: true, reason: ' ' })).toThrow('Reason is required');
  });
});

describe('buildDeleteGrantRequest', () => {
  it('should carry only the reason', () => {
    expect(buildDeleteGrantRequest(' stale ')).toEqual({ reason: 'stale' });
  });

  it('should require a reason', () => {
    expect(() => buildDeleteGrantRequest('')).toThrow('Reason is required');
  });
});
