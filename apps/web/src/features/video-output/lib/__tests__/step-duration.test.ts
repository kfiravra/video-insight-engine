import { describe, it, expect } from 'vitest';

import { parseStepDurationSeconds } from '../step-duration';

describe('parseStepDurationSeconds', () => {
  it('should read a bare number as minutes when the model omits the unit', () => {
    expect(parseStepDurationSeconds(10)).toBe(600);
  });

  it('should read a bare numeric string as minutes', () => {
    expect(parseStepDurationSeconds('3')).toBe(180);
  });

  it('should keep explicit minute units', () => {
    expect(parseStepDurationSeconds('10 min')).toBe(600);
  });

  it('should keep explicit second units', () => {
    expect(parseStepDurationSeconds('30 sec')).toBe(30);
  });

  it('should keep explicit hour units', () => {
    expect(parseStepDurationSeconds('2 hours')).toBe(7200);
  });

  it('should return 0 when there is no duration', () => {
    expect(parseStepDurationSeconds(null)).toBe(0);
  });

  it('should return 0 for text without a number', () => {
    expect(parseStepDurationSeconds('until golden')).toBe(0);
  });

  it('should return 0 for a zero or negative number', () => {
    expect(parseStepDurationSeconds(0)).toBe(0);
  });
});
