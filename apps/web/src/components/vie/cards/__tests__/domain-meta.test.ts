import { describe, it, expect } from 'vitest';
import { trueCountLabel } from '../domain-meta';

describe('trueCountLabel', () => {
  it('should replace the planner count with the shipped count', () => {
    expect(trueCountLabel('9 Core Arguments', 24)).toBe('24 Core Arguments');
  });

  it('should keep a hyphenated count form when replacing', () => {
    expect(trueCountLabel('6-Step Software Factory', 10)).toBe('10-Step Software Factory');
  });

  it('should leave a label without a leading count untouched', () => {
    expect(trueCountLabel('Test Yourself', 5)).toBe('Test Yourself');
  });

  it('should drop a space-separated count when the shipped count is unknown', () => {
    expect(trueCountLabel('12 Key Moments', undefined)).toBe('Key Moments');
  });

  it('should keep a hyphenated number when the shipped count is unknown', () => {
    expect(trueCountLabel('7-Minute Workout', undefined)).toBe('7-Minute Workout');
  });

  it('should not touch a number that is not at the start', () => {
    expect(trueCountLabel('Top 10 Numbers', 24)).toBe('Top 10 Numbers');
  });
});
