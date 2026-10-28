import { describe, it, expect } from 'vitest';
import { formatDateTime, formatDateTimeUtc, formatDuration, timeAgo, formatCost, formatNumber } from './format';

describe('formatDateTimeUtc', () => {
  it('should show the instant in UTC whatever the viewer\'s timezone is', () => {
    expect(formatDateTimeUtc('2026-06-03T23:59:59.000Z')).toMatch(/^Jun 3, 2026, 11:59\sPM UTC$/);
  });

  it('should keep the UTC day for an end-of-day instant a UTC-minus zone would roll back', () => {
    expect(formatDateTimeUtc('2026-12-31T23:59:59.000Z')).toContain('Dec 31, 2026');
  });

  it.each([null, undefined, '', 'not-a-date'])('should return "—" for %s', (value) => {
    expect(formatDateTimeUtc(value)).toBe('—');
  });
});

describe('formatDateTime', () => {
  it('should return "—" for null input', () => {
    expect(formatDateTime(null)).toBe('—');
  });

  it('should return "—" for undefined input', () => {
    expect(formatDateTime(undefined)).toBe('—');
  });

  it('should return "—" for empty string', () => {
    expect(formatDateTime('')).toBe('—');
  });

  it('should return "—" for an invalid date string', () => {
    expect(formatDateTime('not-a-date')).toBe('—');
  });

  it('should include the year for a valid ISO timestamp', () => {
    const result = formatDateTime('2026-06-03T13:00:00.000Z');
    expect(result).toContain('2026');
  });

  it('should include a month abbreviation for a valid ISO timestamp', () => {
    const result = formatDateTime('2026-06-03T13:00:00.000Z');
    // Should contain a month name (Jun in en-US locale)
    expect(result).toMatch(/Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec/);
  });

  it('should include a timezone label (non-empty, non-undefined part)', () => {
    const result = formatDateTime('2026-06-03T13:00:00.000Z');
    // The result must not be just "—" and must contain some tz indicator
    expect(result).not.toBe('—');
    // Should have at least 3 space-separated segments (date, time, tz)
    const parts = result.split(' ');
    expect(parts.length).toBeGreaterThanOrEqual(3);
  });

  it('should handle timestamps at midnight correctly', () => {
    const result = formatDateTime('2026-01-01T00:00:00.000Z');
    expect(result).not.toBe('—');
    expect(result).toContain('2026');
  });

  it('should produce consistent output for the same input', () => {
    const iso = '2026-06-03T08:30:00.000Z';
    expect(formatDateTime(iso)).toBe(formatDateTime(iso));
  });

  it('should produce identical output across many calls (cached formatters)', () => {
    const iso = '2026-06-03T13:00:00.000Z';
    const first = formatDateTime(iso);
    const repeated = Array.from({ length: 200 }, () => formatDateTime(iso));
    expect(repeated.every((r) => r === first)).toBe(true);
  });

  it('should still return "—" for invalid input after formatting valid ones', () => {
    formatDateTime('2026-06-03T13:00:00.000Z');
    expect(formatDateTime('not-a-date')).toBe('—');
  });
});

describe('formatDuration (existing)', () => {
  it('should return "—" for null', () => {
    expect(formatDuration(null)).toBe('—');
  });

  it('should format seconds under a minute', () => {
    expect(formatDuration(45)).toBe('0:45');
  });

  it('should format minutes and seconds', () => {
    expect(formatDuration(125)).toBe('2:05');
  });

  it('should format hours correctly', () => {
    expect(formatDuration(3661)).toBe('1:01:01');
  });
});

describe('timeAgo (existing)', () => {
  it('should return "—" for null', () => {
    expect(timeAgo(null)).toBe('—');
  });

  it('should return "just now" for very recent timestamps', () => {
    expect(timeAgo(new Date().toISOString())).toBe('just now');
  });
});

describe('formatCost (existing)', () => {
  it('should format zero as $0.0000', () => {
    expect(formatCost(0)).toBe('$0.0000');
  });

  it('should format $0.0000 for null', () => {
    expect(formatCost(null)).toBe('$0.0000');
  });
});

describe('formatNumber (existing)', () => {
  it('should return "0" for null', () => {
    expect(formatNumber(null)).toBe('0');
  });

  it('should add locale separators for large numbers', () => {
    const result = formatNumber(1000);
    expect(result).toContain('1');
    expect(result).toContain('000');
  });
});

describe('formatUsageVolume', () => {
  it('should render transcription rows as minutes of audio', async () => {
    const { formatUsageVolume } = await import('./format');
    expect(formatUsageVolume({ unit: 'audio_seconds', audio_seconds: 312, tokens_in: 0, tokens_out: 0 })).toBe('5.2 min audio');
  });

  it('should sum tokens for token-priced rows', async () => {
    const { formatUsageVolume } = await import('./format');
    expect(formatUsageVolume({ unit: 'tokens', tokens_in: 1200, tokens_out: 300 })).toBe('1,500');
    expect(formatUsageVolume({ tokens_in: 5, tokens_out: null })).toBe('5');
  });
});

describe('shortId', () => {
  it('should shorten a long id to its first six and last four characters', async () => {
    const { shortId } = await import('./format');

    expect(shortId('64f1a2b3c4d5e6f708192a3b')).toBe('64f1a2…2a3b');
  });

  it('should leave a short id as it is', async () => {
    const { shortId } = await import('./format');

    expect(shortId('abc123')).toBe('abc123');
  });
});
