import { describe, it, expect } from 'vitest';
import type { TabEntry } from '@vie/types';
import { hasLeadingCount, inferItemCount, stripCountPrefix, trueCountLabel } from '../domain-meta';

describe('trueCountLabel', () => {
  it('should replace the planner count with the shipped count', () => {
    expect(trueCountLabel('9 Core Arguments', 24)).toBe('24 Core Arguments');
  });

  it('should leave a label without a leading count untouched', () => {
    expect(trueCountLabel('Test Yourself', 5)).toBe('Test Yourself');
  });

  it('should drop a space-separated count when the shipped count is unknown', () => {
    expect(trueCountLabel('12 Key Moments', undefined)).toBe('Key Moments');
  });

  it('should not touch a number that is not at the start', () => {
    expect(trueCountLabel('Top 10 Numbers', 24)).toBe('Top 10 Numbers');
  });

  it('should never rewrite a hyphenated leading number (it is a name or a duration)', () => {
    expect(trueCountLabel('6-Step Software Factory', 10)).toBe('6-Step Software Factory');
    expect(trueCountLabel('7-Minute Workout', 12)).toBe('7-Minute Workout');
  });

  it('should keep a hyphenated number when the shipped count is unknown', () => {
    expect(trueCountLabel('7-Minute Workout', undefined)).toBe('7-Minute Workout');
  });

  it('should treat a four-digit leading number as a year, not a count', () => {
    expect(trueCountLabel('2024 Review', 5)).toBe('2024 Review');
    expect(trueCountLabel('2024 Review', undefined)).toBe('2024 Review');
  });

  it('should leave a number-only label alone', () => {
    expect(trueCountLabel('12', 5)).toBe('12');
  });

  // pC review: a Hebrew / Arabic label is a translation whose number came from
  // the assembled (true-count) English label; the English unit list can't tell
  // its measures from its counts, so it is never rewritten.
  it('should leave a Hebrew label as written when its number sits before a Hebrew word', () => {
    expect(trueCountLabel('12 רגעים מרכזיים', 7)).toBe('12 רגעים מרכזיים');
  });

  it('should leave a measure verbatim when a unit follows the number', () => {
    expect(trueCountLabel('7 Days in Tokyo', 21)).toBe('7 Days in Tokyo');
  });

  it('should leave a measure verbatim when the shipped count is unknown', () => {
    expect(trueCountLabel('10 Minute Abs', undefined)).toBe('10 Minute Abs');
  });

  it('should recognise a unit whatever its case', () => {
    expect(trueCountLabel('3 COURSE Meal', 5)).toBe('3 COURSE Meal');
  });

  it('should still rewrite a count whose word only starts like a unit', () => {
    expect(trueCountLabel('8 Secrets of Sleep', 5)).toBe('5 Secrets of Sleep');
  });
});

describe('hasLeadingCount / stripCountPrefix', () => {
  it('should agree on what a count claim is', () => {
    expect(hasLeadingCount('8 Steps')).toBe(true);
    expect(stripCountPrefix('8 Steps')).toBe('Steps');
    expect(hasLeadingCount('5-Star Recipe')).toBe(false);
    expect(stripCountPrefix('5-Star Recipe')).toBe('5-Star Recipe');
    expect(stripCountPrefix('1.5 cups')).toBe('1.5 cups');
  });

  it('should agree that a measure is not a count claim', () => {
    expect(hasLeadingCount('10 Minute Abs')).toBe(false);
    expect(stripCountPrefix('10 Minute Abs')).toBe('10 Minute Abs');
  });
});

describe('inferItemCount', () => {
  function tab(component: string, props: Record<string, unknown>): TabEntry {
    return { id: 't', label: 'T', emoji: '', component, props };
  }

  it('should count the registered list prop of a component', () => {
    expect(inferItemCount(tab('spot_explorer', { spots: [{}, {}, {}] }))).toBe(3);
  });

  it('should sum rows, pros and cons for comparison and comparison_radar alike', () => {
    const props = { comparisons: [{}, {}], pros: ['a'], cons: ['b', 'c'] };
    expect(inferItemCount(tab('comparison', props))).toBe(5);
    expect(inferItemCount(tab('comparison_radar', props))).toBe(5);
  });

  it('should return undefined for an unknown component', () => {
    expect(inferItemCount(tab('mystery', { items: [{}] }))).toBeUndefined();
  });
});
