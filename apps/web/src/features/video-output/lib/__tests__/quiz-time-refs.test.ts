import { describe, it, expect } from 'vitest';

import { splitTimeRefs } from '../quiz-time-refs';

describe('splitTimeRefs', () => {
  it('should split text around an (m:ss) reference into seconds', () => {
    expect(splitTimeRefs('Brownie (11:20) moment.')).toEqual([
      { kind: 'text', text: 'Brownie ' },
      { kind: 'seek', seconds: 680 },
      { kind: 'text', text: ' moment.' },
    ]);
  });

  it('should parse (h:mm:ss) references', () => {
    expect(splitTimeRefs('(1:02:05)')).toEqual([{ kind: 'seek', seconds: 3725 }]);
  });

  it('should return the whole text when there is no reference', () => {
    expect(splitTimeRefs('Plain explanation.')).toEqual([{ kind: 'text', text: 'Plain explanation.' }]);
  });

  it('should ignore parenthesised text that is not a time', () => {
    expect(splitTimeRefs('ratio (3:1) and (v2:0)')).toEqual([{ kind: 'text', text: 'ratio (3:1) and (v2:0)' }]);
  });

  it('should handle several references in one explanation', () => {
    expect(splitTimeRefs('(0:05) then (2:40)').filter((s) => s.kind === 'seek')).toHaveLength(2);
  });

  it('should reject seconds or minutes of 60 and above', () => {
    expect(splitTimeRefs('(12:60) and (99:99:99)')).toEqual([{ kind: 'text', text: '(12:60) and (99:99:99)' }]);
  });

  it('should split adjacent references into two seeks with no empty text between them', () => {
    expect(splitTimeRefs('(1:00)(2:00)')).toEqual([
      { kind: 'seek', seconds: 60 },
      { kind: 'seek', seconds: 120 },
    ]);
  });
});
