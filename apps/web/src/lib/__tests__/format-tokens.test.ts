import { describe, it, expect } from 'vitest';
import {
  fillTemplate,
  formatDate,
  formatDurationSeconds,
  formatDurationUntil,
  formatNumber,
  formatPlural,
} from '../format-tokens';
import { getLabels } from '../i18n';

const NOW = Date.UTC(2026, 9, 28, 12, 0, 0);
const minutesIn = (n: number) => new Date(NOW + n * 60_000).toISOString();

describe('formatPlural', () => {
  describe('tokens in English', () => {
    it.each([
      [0, '0 tokens'],
      [1, '1 token'],
      [2, '2 tokens'],
      [840, '840 tokens'],
      [20000, '20,000 tokens'],
      [-120, '-120 tokens'],
    ])('should format %i as "%s"', (count, expected) => {
      expect(formatPlural(count, 'en', getLabels('en').billing.tokens)).toBe(expected);
    });
  });

  describe('minutes in Hebrew', () => {
    it.each([
      [1, 'דקה אחת'],
      [2, 'שתי דקות'],
      [3, '3 דקות'],
      [11, '11 דקות'],
      [150, '150 דקות'],
    ])('should format %i as "%s"', (count, expected) => {
      expect(formatPlural(count, 'he', getLabels('he').billing.minutes)).toBe(expected);
    });
  });

  describe('minutes in Arabic', () => {
    it.each([
      [0, '0 دقيقة'],
      [1, 'دقيقة واحدة'],
      [2, 'دقيقتان'],
      [3, '3 دقائق'],
      [10, '10 دقائق'],
      [11, '11 دقيقة'],
      [100, '100 دقيقة'],
    ])('should format %i as "%s"', (count, expected) => {
      expect(formatPlural(count, 'ar', getLabels('ar').billing.minutes)).toBe(expected);
    });
  });

  it('should pick the Arabic category from Intl.PluralRules, so the tokens table only fills forms', () => {
    const forms = { zero: 'z {count}', one: 'o', two: 't', few: 'f {count}', many: 'm {count}', other: 'x {count}' };

    expect([0, 1, 2, 3, 11, 100].map((count) => formatPlural(count, 'ar', forms))).toEqual([
      'z 0',
      'o',
      't',
      'f 3',
      'm 11',
      'x 100',
    ]);
  });

  // The he/ar number formats put a left-to-right mark before the minus, so the
  // expected text is built with formatNumber rather than typed.
  it.each([
    ['he', -1],
    ['he', -2],
    ['he', -120],
    ['ar', -1],
    ['ar', -2],
  ])('should keep the sign of a %s balance in debt (%i), in the form that carries the number', (language, count) => {
    const forms = getLabels(language).billing.tokens;

    expect(formatPlural(count, language, forms)).toBe(forms.other.replace('{count}', formatNumber(count, language)));
  });

  it('should keep the singular for an English -1', () => {
    expect(formatPlural(-1, 'en', getLabels('en').billing.tokens)).toBe('-1 token');
  });

  it('should fall back to the other form when a category has no form', () => {
    expect(formatPlural(2, 'he', { other: '{count} x' })).toBe('2 x');
  });
});

describe('formatNumber', () => {
  it.each(['en', 'he', 'ar'])('should use Latin digits and grouping in %s', (language) => {
    expect(formatNumber(1250, language)).toBe('1,250');
  });

  it('should print a balance in debt with its sign', () => {
    expect(formatNumber(-1250, 'en')).toBe('-1,250');
  });
});

describe('fillTemplate', () => {
  it('should replace every placeholder', () => {
    expect(fillTemplate('{a} of {b}', { a: 1, b: '2 tokens' })).toBe('1 of 2 tokens');
  });

  it('should leave a placeholder without a value as written', () => {
    expect(fillTemplate('resets in {when}', {})).toBe('resets in {when}');
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

describe('formatDurationSeconds', () => {
  const en = getLabels('en').billing.duration;

  it.each([
    [45 * 60, '45m'],
    [2 * 3600, '2h'],
    [2 * 3600 + 5 * 60, '2h 5m'],
    [90_000, '1d 1h'],
  ])('should format a wait of %i seconds as "%s"', (seconds, expected) => {
    expect(formatDurationSeconds(seconds, NOW, 'en', en)).toBe(expected);
  });

  it('should round a wait under a minute up to one minute', () => {
    expect(formatDurationSeconds(20, NOW, 'en', en)).toBe('1m');
  });

  it.each([null, undefined, 0, -5, Number.NaN, Number.POSITIVE_INFINITY])('should return null for %s', (seconds) => {
    expect(formatDurationSeconds(seconds, NOW, 'en', en)).toBeNull();
  });

  it('should use the language unit templates', () => {
    expect(formatDurationSeconds(2 * 3600, NOW, 'ar', getLabels('ar').billing.duration)).toBe('2 س');
  });
});
