import { describe, it, expect, vi, beforeEach } from 'vitest';
import type { Dispatch, SetStateAction } from 'react';
import { processEvent } from '@/features/video-output/lib/streaming/stream-event-processor';
import type { StreamState } from '@/features/video-output/hooks/use-summary-stream';

// Keep the validator's invalid-event warnings out of the test output.
vi.mock('@/features/video-output/lib/streaming/sse-logger', () => ({
  sseLogger: { warn: vi.fn(), error: vi.fn(), info: vi.fn() },
}));

// ─────────────────────────────────────────────────────
// Helpers
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
};

/** Runs events through processEvent the way React applies the updaters. */
function runEvents(events: Record<string, unknown>[]): StreamState {
  let state = initialState;
  const setState: Dispatch<SetStateAction<StreamState>> = (action) => {
    state = typeof action === 'function' ? action(state) : action;
  };
  for (const event of events) processEvent(event, setState);
  return state;
}

/** memory-done emission: only the hero fields. */
function earlyEvent(overrides: Record<string, unknown> = {}): Record<string, unknown> {
  return {
    event: 'synthesis_complete',
    tldr: 'Memory tldr',
    keyTakeaways: ['Memory point 1', 'Memory point 2'],
    ...overrides,
  };
}

/** synthesis-done emission: the full superset. */
function lateEvent(overrides: Record<string, unknown> = {}): Record<string, unknown> {
  return {
    event: 'synthesis_complete',
    tldr: 'Final tldr',
    keyTakeaways: ['Final point 1', 'Final point 2', 'Final point 3'],
    masterSummary: 'The full master summary.',
    seoDescription: 'SEO description.',
    ...overrides,
  };
}

const FULL_SYNTHESIS = {
  tldr: 'Final tldr',
  keyTakeaways: ['Final point 1', 'Final point 2', 'Final point 3'],
  masterSummary: 'The full master summary.',
  seoDescription: 'SEO description.',
};

// ─────────────────────────────────────────────────────
// synthesis_complete
// ─────────────────────────────────────────────────────

describe('stream-event-processor — synthesis_complete', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  describe('single emission', () => {
    it('should expose the hero fields when only the early emission arrives', () => {
      const state = runEvents([earlyEvent()]);

      expect(state.synthesis).toEqual({
        tldr: 'Memory tldr',
        keyTakeaways: ['Memory point 1', 'Memory point 2'],
        masterSummary: '',
        seoDescription: '',
      });
    });

    it('should store every field when only the late emission arrives', () => {
      const state = runEvents([lateEvent()]);

      expect(state.synthesis).toEqual(FULL_SYNTHESIS);
    });

    it('should default seoDescription to an empty string when the emission lacks it', () => {
      const state = runEvents([lateEvent({ seoDescription: undefined })]);

      expect(state.synthesis?.seoDescription).toBe('');
    });
  });

  describe('two emissions', () => {
    it('should let the superset overwrite the early fields when early arrives before late', () => {
      const state = runEvents([earlyEvent(), lateEvent()]);

      expect(state.synthesis).toEqual(FULL_SYNTHESIS);
    });

    it('should keep the full synthesis when a replayed early emission arrives after the late one', () => {
      const state = runEvents([lateEvent(), earlyEvent()]);

      expect(state.synthesis).toEqual(FULL_SYNTHESIS);
    });

    it('should keep the full synthesis when the early emission is replayed around it', () => {
      const state = runEvents([earlyEvent(), lateEvent(), earlyEvent()]);

      expect(state.synthesis).toEqual(FULL_SYNTHESIS);
    });

    it('should keep the early hero fields when a later emission leaves them empty', () => {
      const state = runEvents([earlyEvent(), lateEvent({ tldr: '', keyTakeaways: [] })]);

      expect(state.synthesis).toEqual({
        tldr: 'Memory tldr',
        keyTakeaways: ['Memory point 1', 'Memory point 2'],
        masterSummary: 'The full master summary.',
        seoDescription: 'SEO description.',
      });
    });

    it('should keep the early hero fields when synthesis fails and emits an all-empty event', () => {
      const failed = { event: 'synthesis_complete', tldr: '', keyTakeaways: [], masterSummary: '', seoDescription: '' };

      const state = runEvents([earlyEvent(), failed]);

      expect(state.synthesis?.tldr).toBe('Memory tldr');
    });

    it('should let a newer full emission replace an older one when the full dict is re-sent', () => {
      const state = runEvents([lateEvent(), lateEvent({ masterSummary: 'Revised summary.' })]);

      expect(state.synthesis?.masterSummary).toBe('Revised summary.');
    });
  });
});
