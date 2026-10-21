import { describe, it, expect, vi, beforeEach } from 'vitest';
import type { Dispatch, SetStateAction } from 'react';
import { processEvent } from '@/features/video-output/lib/streaming/stream-event-processor';
import { validateSynthesisComplete } from '@/features/video-output/lib/streaming/sse-validators';
import type { StreamState } from '@/features/video-output/hooks/use-summary-stream';

// Keep the validator's invalid-event warnings out of the test output.
vi.mock('@/features/video-output/lib/streaming/sse-logger', () => ({
  sseLogger: { warn: vi.fn(), error: vi.fn(), info: vi.fn() },
}));

// ─────────────────────────────────────────────────────
// Helpers — the hero rewrite's emissions (output-quality 2.2)
// ─────────────────────────────────────────────────────

const initialState: StreamState = {
  phase: 'idle',
  phaseDetail: null,
  metadata: null,
  duration: null,
  error: null,
  isCached: false,
  processingTimeMs: null,
  degraded: false,
  warnings: [],
  confettiCount: 0,
  triage: null,
  extractionProgress: null,
  domainData: null,
  enrichment: null,
  synthesis: null,
  meta: null,
  tabs: [],
  tabCount: 0,
  tabLabels: [],
  frames: [],
  finalTabIds: null,
  replacedTabIds: {},
};

function runEvents(events: Record<string, unknown>[]): StreamState {
  let state = initialState;
  const setState: Dispatch<SetStateAction<StreamState>> = (action) => {
    state = typeof action === 'function' ? action(state) : action;
  };
  for (const event of events) processEvent(event, setState);
  return state;
}

/** memory-done: the ≤ 16 s preview hero. */
const memoryPartial = {
  event: 'synthesis_complete',
  tldr: 'Memory tldr',
  keyTakeaways: ['Memory point [0:42]'],
};

/** synthesis-done: the hero rewrite's superset (no reading time yet). */
const heroSuperset = {
  event: 'synthesis_complete',
  tldr: 'Hand-written code no longer pays.',
  keyTakeaways: ['Agents write the code now. [1:00]', 'Review is the job. [1:02:05]'],
  masterSummary: 'It opens on a bet. It ends on a promise.',
  seoDescription: 'A keynote on agents writing the code.',
  brief: 'It opens on a bet. It ends on a promise.',
  worthItFor: 'Leads deciding how far to let agents in.',
  skipIf: 'You want a how-to: no code is written on stage.',
};

/** after the late group: the corrected superset with the page's reading time. */
const settledSuperset = { ...heroSuperset, readingMinutes: 5 };

// ─────────────────────────────────────────────────────
// synthesis_complete with the hero rewrite
// ─────────────────────────────────────────────────────

describe('stream-event-processor — the hero rewrite on synthesis_complete', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it('should replace the preview hero with the hero rewrite when its superset arrives', () => {
    const state = runEvents([memoryPartial, heroSuperset]);

    expect(state.synthesis?.tldr).toBe('Hand-written code no longer pays.');
  });

  it('should expose the brief and both lines when the hero superset arrives', () => {
    const state = runEvents([memoryPartial, heroSuperset]);

    expect({
      brief: state.synthesis?.brief,
      worthItFor: state.synthesis?.worthItFor,
      skipIf: state.synthesis?.skipIf,
    }).toEqual({
      brief: heroSuperset.brief,
      worthItFor: heroSuperset.worthItFor,
      skipIf: heroSuperset.skipIf,
    });
  });

  it('should keep the takeaways as strings closing with their marker', () => {
    const state = runEvents([memoryPartial, heroSuperset]);

    expect(state.synthesis?.keyTakeaways).toEqual(heroSuperset.keyTakeaways);
  });

  it('should take the reading time from the corrected superset after the late group', () => {
    const state = runEvents([memoryPartial, heroSuperset, settledSuperset]);

    expect(state.synthesis?.readingMinutes).toBe(5);
  });

  it('should keep the hero fields when a replayed memory partial arrives after them', () => {
    const state = runEvents([memoryPartial, settledSuperset, memoryPartial]);

    expect(state.synthesis).toEqual({
      tldr: settledSuperset.tldr,
      keyTakeaways: settledSuperset.keyTakeaways,
      masterSummary: settledSuperset.masterSummary,
      seoDescription: settledSuperset.seoDescription,
      brief: settledSuperset.brief,
      worthItFor: settledSuperset.worthItFor,
      skipIf: settledSuperset.skipIf,
      readingMinutes: 5,
    });
  });

  it('should carry no hero field when the run wrote the hero with synthesis', () => {
    const synthesisSuperset = {
      event: 'synthesis_complete',
      tldr: 'Memory tldr',
      keyTakeaways: ['Memory point [0:42]'],
      masterSummary: 'The full master summary.',
      seoDescription: 'SEO description.',
    };

    const state = runEvents([memoryPartial, synthesisSuperset]);

    expect(Object.keys(state.synthesis ?? {}).sort()).toEqual([
      'keyTakeaways',
      'masterSummary',
      'seoDescription',
      'tldr',
    ]);
  });
});

describe('validateSynthesisComplete — hero fields', () => {
  it('should drop an emission whose readingMinutes is not a whole positive number', () => {
    expect(validateSynthesisComplete({ ...settledSuperset, readingMinutes: 0 })).toEqual({});
  });

  it('should drop an emission whose readingMinutes is null', () => {
    expect(validateSynthesisComplete({ ...settledSuperset, readingMinutes: null })).toEqual({});
  });

  it('should leave out an empty hero line so it never blanks an earlier one', () => {
    expect(validateSynthesisComplete({ ...heroSuperset, worthItFor: '' })).not.toHaveProperty('worthItFor');
  });
});
