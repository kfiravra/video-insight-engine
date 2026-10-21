import { describe, it, expect } from 'vitest';
import { TAB_PROP_SCHEMAS } from '@/features/video-output/lib/tab-prop-schemas';

// output-quality 1.1 / 1.3: a key-idea spot card carries its kind as `category`
// and a podcast position its holder as `speaker`; the tab schema declares both.

function parsesSpots(spots: Array<Record<string, unknown>>): boolean {
  return TAB_PROP_SCHEMAS.spot_explorer.schema.safeParse({ spots }).success;
}

describe('spot_explorer schema — attribution fields', () => {
  it('should accept a position card when it names its speaker and kind', () => {
    expect(
      parsesSpots([
        { name: 'Start with goals, not risks', speaker: 'Max Tegmark', category: 'Thesis', timestamp: 4801 },
        { name: 'Intelligence is substrate-independent', speaker: null, category: null },
      ]),
    ).toBe(true);
  });

  it('should reject a card whose speaker is not text', () => {
    expect(parsesSpots([{ name: 'A claim', speaker: 42 }])).toBe(false);
  });
});
