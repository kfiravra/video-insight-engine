import { describe, it, expect } from 'vitest';
import { momentsToListenSegments } from '../flow-mode-helpers';
import type { MomentItem } from '@vie/types';

const ITEMS: MomentItem[] = [
  { time: '2:28', seconds: 148, label: 'Intelligent life', description: 'Guess: no.' },
  { time: '5:44', seconds: 344, label: 'Great Filter', speaker: 'Tegmark' },
  { time: '9:00', seconds: 540, endSeconds: 600, label: 'Clip' },
];

describe('momentsToListenSegments', () => {
  it('should end a segment where the next one starts', () => {
    expect(momentsToListenSegments(ITEMS)[0].endSeconds).toBe(344);
  });

  it('should keep an explicit endSeconds over the next start', () => {
    expect(momentsToListenSegments(ITEMS)[2].endSeconds).toBe(600);
  });

  it('should leave the last segment open-ended', () => {
    expect(momentsToListenSegments(ITEMS.slice(0, 2))[1].endSeconds).toBeUndefined();
  });

  it('should carry description and speaker through', () => {
    const [first, second] = momentsToListenSegments(ITEMS);
    expect([first.description, second.speaker]).toEqual(['Guess: no.', 'Tegmark']);
  });

  it('should ignore an explicit endSeconds that does not come after the start', () => {
    const out = momentsToListenSegments([{ ...ITEMS[0], endSeconds: 100 }, ITEMS[1]]);
    expect(out[0].endSeconds).toBe(344);
  });

  it('should not produce a negative range when items are out of order', () => {
    const out = momentsToListenSegments([ITEMS[1], ITEMS[0]]);
    expect(out[0].endSeconds).toBeUndefined();
  });
});
