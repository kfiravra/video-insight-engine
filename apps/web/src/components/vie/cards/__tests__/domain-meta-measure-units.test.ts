import { describe, it, expect } from 'vitest';
import { MEASURE_UNITS, rawConfig } from '@vie/shared/config';
import { hasLeadingCount } from '../domain-meta';

// output-quality 1.8: the measure-unit list is domains.json `measureUnits`, the
// one list the summarizer's labels.py and the output-quality tools read too.

describe('measure units — one list in the registry', () => {
  it('should read every unit the registry lists', () => {
    expect([...MEASURE_UNITS].sort()).toEqual([...(rawConfig.measureUnits ?? [])].sort());
  });

  it('should read a registry unit as a measure, not a count claim', () => {
    expect(hasLeadingCount('7 Days in Tokyo')).toBe(false);
  });

  it('should still read a plain noun as a count claim', () => {
    expect(hasLeadingCount('12 Key Ideas')).toBe(true);
  });
});
