import { describe, it, expect } from 'vitest';
import {
  fillTemplate,
  formatDate,
  formatDurationUntil,
  formatMinutes,
  formatNumber,
  formatPlural,
} from '../format-minutes';
import { getLabels } from '../i18n';

const NOW = Date.UTC(2026, 9, 28, 12, 0, 0);
const minutesIn = (n: number) => new Date(NOW + n * 60_000).toISOString();

describe('formatMinutes', () => {
  describe('English', () => {
    it.each([
      [0, '0 minutes'],
      [1, '1 minute'],
      [2, '2 minutes'],
      [150, '150 minutes'],
      [1250, '1,250 minutes'],
    ])('should format %i as "%s"', (count, expected) => {
      expect(formatMinutes(count, 'en', getLabels('en').billing.minutes)).toBe(expected);
    });
  });

  describe('Hebrew', () => {
    it.each([
      [1, 'דקה אחת'],
      [2, 'שתי דקות'],
      [3, '3 דקות'],
      [11, '11 דקות'],
      [150, '150 דקות'],
    ])('should format %i as "%s"', (count, expected) => {
      expect(formatMinutes(count, 'he', getLabels('he').billing.minutes)).toBe(expected);
    });
  });

  describe('Arabic', () => {
    it.each([
      [0, '0 دقيقة'],
      [1, 'دقيقة واحدة'],
      [2, 'دقيقتان'],
      [3, '3 دقائق'],
      [10, '10 دقائق'],
      [11, '11 دقيقة'],
      [100, '100 دقيقة'],
    ])('should format %i as "%s"', (count, expected) => {
      expect(formatMinutes(count, 'ar', getLabels('ar').billing.minutes)).toBe(expected);
    });
  });

  it('should fall back to the other form when a category has no form', () => {
    expect(formatPlural(2, 'he', { other: '{count} x' })).toBe('2 x');
  });
});

describe('formatNumber', () => {
  it.each(['en', 'he', 'ar'])('should use Latin digits and grouping in %s', (language) => {
    expect(formatNumber(1250, language)).toBe('1,250');
  });
});

describe('fillTemplate', () => {
  it('should replace every placeholder', () => {
    expect(fillTemplate('{a} of {b}', { a: 1, b: '2 minutes' })).toBe('1 of 2 minutes');
  });

  it('should leave a placeholder without a value as written', () => {
    expect(fillTemplate('next in {when}', {})).toBe('next in {when}');
  });
});

describe('formatDurationUntil', () => {
  const en = getLabels('en').billing.duration;

  it.each([
    [30, '30m'],
    [60, '1h'],
    [7 * 60 + 30, '7h 30m'],
    [24 * 60, '1d'],
    [2 * 24 * 60 + 3 * 60 + 25, '2d 3h'],
  ])('should format %i minutes ahead as "%s"', (minutes, expected) => {
    expect(formatDurationUntil(minutesIn(minutes), NOW, 'en', en)).toBe(expected);
  });

  it('should round a few seconds ahead up to one minute', () => {
    expect(formatDurationUntil(new Date(NOW + 5_000).toISOString(), NOW, 'en', en)).toBe('1m');
  });

  it('should return null for a time already past', () => {
    expect(formatDurationUntil(minutesIn(-1), NOW, 'en', en)).toBeNull();
  });

  it('should return null for an unparseable time', () => {
    expect(formatDurationUntil('soon', NOW, 'en', en)).toBeNull();
  });

  it('should use the language unit templates', () => {
    expect(formatDurationUntil(minutesIn(7 * 60 + 30), NOW, 'he', getLabels('he').billing.duration)).toBe(
      '7 שע׳ 30 דק׳',
    );
  });
});

describe('formatDate', () => {
  it('should format an ISO date in the language date style', () => {
    expect(formatDate('2026-11-01T12:00:00.000Z', 'en')).toBe('Nov 1, 2026');
  });

  it.each([null, undefined, '', 'not a date'])('should print a dash for %s', (value) => {
    expect(formatDate(value, 'en')).toBe('—');
  });
});
