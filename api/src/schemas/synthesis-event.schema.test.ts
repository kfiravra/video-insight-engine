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

    it('should drop a readingMinutes that is not a whole positive number and keep the event', () => {
      const result = synthesisCompleteEventSchema.safeParse({ ...heroSuperset, readingMinutes: 0 });

      expect(result.success && result.data.readingMinutes).toBeUndefined();
    });

    it('should keep the rest of the superset when readingMinutes is null', () => {
      const result = synthesisCompleteEventSchema.safeParse({ ...heroSuperset, readingMinutes: null });

      expect(result.success && result.data.tldr).toBe(heroSuperset.tldr);
    });

    it.each(['brief', 'worthItFor', 'skipIf'] as const)('should drop a %s that is not a string and keep the event', (key) => {
      const result = synthesisCompleteEventSchema.safeParse({ ...heroSuperset, [key]: null });

      expect(result.success && [result.data[key], result.data.tldr]).toEqual([undefined, heroSuperset.tldr]);
    });

    it('should keep the final flag when the corrected superset carries it', () => {
      const result = synthesisCompleteEventSchema.safeParse({ ...heroSuperset, final: true });

      expect(result.success && result.data.final).toBe(true);
    });

    it('should drop a final flag that is not a boolean and keep the event', () => {
      const result = synthesisCompleteEventSchema.safeParse({ ...heroSuperset, final: 'yes' });

      expect(result.success && result.data.final).toBeUndefined();
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
