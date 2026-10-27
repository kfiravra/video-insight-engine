import { describe, it, expect } from 'vitest';
import { alertCost, describeAlert, getSeverity } from './alerts';

describe('getSeverity', () => {
  it('should prefer an explicit severity field', () => {
    expect(getSeverity({ type: 'high_cost_call', severity: 'critical' })).toBe('critical');
  });

  it.each([
    ['high_cost_call', 'warning'],
    ['daily_spend_spike', 'warning'],
    ['daily_global_cost', 'critical'],
    ['high_failure_rate', 'critical'],
    ['backup_stale', 'critical'],
    ['pipeline_stalled', 'critical'],
  ])('should map legacy %s rows (no severity field) to %s', (type, expected) => {
    expect(getSeverity({ type })).toBe(expected);
  });

  it('should fall back to keyword matching for unknown types', () => {
    expect(getSeverity({ type: 'worker_down' })).toBe('critical');
    expect(getSeverity({ type: 'cost_spike' })).toBe('warning');
    expect(getSeverity({ type: 'something_else' })).toBe('info');
  });
});

describe('alertCost', () => {
  it('should expose cost only for spend alerts', () => {
    expect(alertCost({ type: 'high_cost_call', cost_usd: 0.91 })).toBe(0.91);
    expect(alertCost({ type: 'daily_spend_spike', cost_usd: 12.5 })).toBe(12.5);
    expect(alertCost({ type: 'daily_global_cost', today_usd: 21.5 })).toBe(21.5);
    expect(alertCost({ type: 'high_failure_rate', cost_usd: 0 })).toBeNull();
    expect(alertCost({ type: 'backup_stale' })).toBeNull();
  });
});

describe('describeAlert', () => {
  it('should describe a high-cost call with model, feature and threshold', () => {
    expect(
      describeAlert({ type: 'high_cost_call', model: 'anthropic/claude-sonnet-4-6', feature: 'summarize:plan', threshold: 0.5 }),
    ).toBe('anthropic/claude-sonnet-4-6 · summarize:plan · over $0.50');
  });

  it('should describe a spend spike from its baseline', () => {
    expect(describeAlert({ type: 'daily_spend_spike', baseline_daily_usd: 2, threshold: 2, day_fraction: 0.5 })).toBe(
      'baseline $2.00/day · 2× limit · 12h into day',
    );
  });

  it('should describe the global cost alarm with its threshold', () => {
    expect(describeAlert({ type: 'daily_global_cost', today_usd: 21.5, threshold_usd: 20 })).toBe(
      'global spend today · alarm at $20.00',
    );
  });

  it('should describe a failure rate with its sample', () => {
    expect(describeAlert({ type: 'high_failure_rate', failure_rate: 0.35, sample_size: 20, window_minutes: 60 })).toBe(
      '35% failed of 20 calls / 60 min',
    );
  });

  it('should describe a stale backup and a missing backup differently', () => {
    expect(describeAlert({ type: 'backup_stale', age_hours: 30.2, max_age_hours: 26 })).toBe('last backup 30.2h ago (max 26h)');
    expect(describeAlert({ type: 'backup_stale', age_hours: null })).toBe('no backup found');
  });

  it('should describe a stalled pipeline by video', () => {
    expect(describeAlert({ type: 'pipeline_stalled', youtube_id: 'dQw4w9WgXcQ', stalled_minutes: 45 })).toBe(
      'dQw4w9WgXcQ idle 45 min → failed',
    );
  });
});
