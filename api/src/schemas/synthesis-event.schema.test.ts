import { describe, it, expect } from 'vitest';
import { synthesisCompleteEventSchema } from './synthesis-event.schema.js';

/** The hero rewrite's corrected superset (output-quality 2.2): the four fields + its own. */
const heroSuperset = {
  event: 'synthesis_complete',
  tldr: 'Hand-written code no longer pays.',
  keyTakeaways: ['Agents write the code now. [1:00]', 'Review is the job. [1:02:05]'],
  masterSummary: 'It opens on a bet. It ends on a promise.',
  seoDescription: 'A keynote on agents writing the code.',
  brief: 'It opens on a bet. It ends on a promise.',
  worthItFor: 'Leads deciding how far to let agents in.',
  skipIf: 'You want a how-to: no code is written on stage.',
  readingMinutes: 5,
};

describe('synthesisCompleteEventSchema', () => {
  describe('the hero rewrite superset', () => {
    it('should keep every hero field when the superset carries them', () => {
      const result = synthesisCompleteEventSchema.safeParse(heroSuperset);

      expect(result.success && result.data).toEqual(heroSuperset);
    });

    it('should accept the superset without readingMinutes when the tabs are not final yet', () => {
      const { readingMinutes: _counted, ...early } = heroSuperset;

      expect(synthesisCompleteEventSchema.safeParse(early).success).toBe(true);
    });

    it('should reject a readingMinutes that is not a whole positive number', () => {
      expect(synthesisCompleteEventSchema.safeParse({ ...heroSuperset, readingMinutes: 0 }).success).toBe(false);
    });

    it('should reject a null readingMinutes', () => {
      expect(synthesisCompleteEventSchema.safeParse({ ...heroSuperset, readingMinutes: null }).success).toBe(false);
    });
  });

  describe('older emissions', () => {
    it('should accept the four-field synthesis superset when no hero field is sent', () => {
      const { brief: _b, worthItFor: _w, skipIf: _s, readingMinutes: _r, ...synthesis } = heroSuperset;

      expect(synthesisCompleteEventSchema.safeParse(synthesis).success).toBe(true);
    });

    it('should keep takeaways as strings when an emission sends objects', () => {
      const objects = { ...heroSuperset, keyTakeaways: [{ text: 'A claim', timestamp: 60 }] };

      expect(synthesisCompleteEventSchema.safeParse(objects).success).toBe(false);
    });
  });
});
