import { describe, it, expect, vi } from 'vitest';
import type { Dispatch, SetStateAction } from 'react';
import { processEvent } from '@/features/video-output/lib/streaming/stream-event-processor';
import { isStreamSettled } from '@/features/video-output/lib/streaming/stream-tabs';
import { INITIAL_STREAM_STATE, type StreamState } from '@/features/video-output/hooks/use-summary-stream';

// Keep the validators' invalid-event warnings out of the test output.
vi.mock('@/features/video-output/lib/streaming/sse-logger', () => ({
  sseLogger: { warn: vi.fn(), error: vi.fn(), info: vi.fn() },
}));

// The tab_ready emission contract (docs/API-REFERENCE.md#tab_ready) as the
// reducer applies it — one describe per pipeline-1min 2.2 rule.

type Event = Record<string, unknown>;

/** Runs events through processEvent the way React applies the updaters. */
function run(events: Event[], from: StreamState = INITIAL_STREAM_STATE): StreamState {
  let state = from;
  const setState: Dispatch<SetStateAction<StreamState>> = (action) => {
    state = typeof action === 'function' ? action(state) : action;
  };
  for (const event of events) processEvent(event, setState);
  return state;
}

function tab(id: string, position: number, extra: Event = {}): Event {
  return {
    event: 'tab_ready',
    id,
    label: id,
    emoji: '•',
    component: 'display_section',
    props: { data: { id } },
    position,
    ...extra,
  };
}

const ids = (state: StreamState): string[] => state.tabs.map((t) => t.id);

describe('stream-event-processor — tab_ready contract', () => {
  describe('meta never wipes tabs', () => {
    it('should keep streamed tabs when meta arrives after them', () => {
      const state = run([tab('moments', 2, { provisional: true }), { event: 'meta', tabCount: 3, tabLabels: [] }]);

      expect(ids(state)).toEqual(['moments']);
    });
  });

  describe('position is a sort key', () => {
    it('should order tabs by position when they arrive out of order', () => {
      const state = run([tab('moments', 2), tab('overview', 0), tab('facts', 3), tab('steps', 1)]);

      expect(ids(state)).toEqual(['overview', 'steps', 'moments', 'facts']);
    });

    it('should move a re-emitted tab to its new position when the position changes', () => {
      const state = run([tab('a', 0), tab('b', 1), tab('c', 2), tab('a', 3, { final: true })]);

      expect(ids(state)).toEqual(['b', 'c', 'a']);
    });

    it('should place a final emission before a non-final one at the same position', () => {
      const state = run([tab('fallback', 2), tab('moments', 2, { final: true })]);

      expect(ids(state)).toEqual(['moments', 'fallback']);
    });

    it('should keep arrival order between non-final tabs that share a position', () => {
      const state = run([tab('moments', 2, { provisional: true }), tab('facts', 2)]);

      expect(ids(state)).toEqual(['moments', 'facts']);
    });
  });

  describe('replace-by-id honours position and final', () => {
    it('should replace the provisional tab by id when the real tab arrives', () => {
      const state = run([tab('moments', 3, { provisional: true }), tab('moments', 2, { final: true })]);

      expect(state.tabs).toEqual([expect.objectContaining({ id: 'moments', position: 2, final: true })]);
    });

    it('should drop the provisional flag when the real tab replaces it', () => {
      const state = run([tab('moments', 3, { provisional: true }), tab('moments', 3)]);

      expect(state.tabs[0].provisional).toBeUndefined();
    });

    it('should ignore a non-final emission for a tab already final', () => {
      const state = run([tab('a', 0, { final: true, label: 'Final' }), tab('a', 0, { label: 'Stale replay' })]);

      expect(state.tabs[0].label).toBe('Final');
    });

    it('should replace a final tab when a later final tab_ready arrives for the same id', () => {
      const state = run([tab('a', 0, { final: true, label: 'First final' }), tab('a', 0, { final: true, label: 'Corrected' })]);

      expect(state.tabs[0].label).toBe('Corrected');
    });

    it('should ignore a provisional emission for a tab that already holds real content', () => {
      const state = run([tab('moments', 2, { label: 'Real' }), tab('moments', 2, { provisional: true, label: 'Placeholder' })]);

      expect(state.tabs[0].label).toBe('Real');
    });

    it('should keep the content objects when a re-emit carries the same content', () => {
      const first = run([tab('a', 0)]);

      const second = run([tab('a', 0, { final: true })], first);

      expect(second.tabs[0].props).toBe(first.tabs[0].props);
    });

    it('should return the same state when an identical emission is replayed', () => {
      const first = run([tab('a', 0, { final: true })]);

      const second = run([tab('a', 0, { final: true })], first);

      expect(second).toBe(first);
    });

    it('should carry goal, attachments and degradedFrom from the event', () => {
      const attachments = [{ slot: 'top', component: 'tip_callout', props: { text: 'Tip' } }];
      const state = run([tab('a', 0, { goal: 'Learn it', attachments, degradedFrom: 'step_player' })]);

      expect(state.tabs[0]).toEqual(
        expect.objectContaining({ goal: 'Learn it', attachments, degradedFrom: 'step_player' }),
      );
    });
  });

  describe('replaces supersedes another id', () => {
    const provisional = tab('key_moments', 3, { provisional: true });

    it('should drop the superseded tab when a tab arrives with replaces', () => {
      const state = run([provisional, tab('chapters', 2, { replaces: 'key_moments' })]);

      expect(ids(state)).toEqual(['chapters']);
    });

    it("should give the replacing tab the superseded tab's arrival slot among equal positions", () => {
      const state = run([provisional, tab('facts', 3), tab('chapters', 3, { replaces: 'key_moments' })]);

      expect(ids(state)).toEqual(['chapters', 'facts']);
    });

    it('should record the superseded id so focus can follow it', () => {
      const state = run([provisional, tab('chapters', 2, { replaces: 'key_moments' })]);

      expect(state.replacedTabIds).toEqual({ key_moments: 'chapters' });
    });

    it('should ignore replaces when the named tab is final', () => {
      const state = run([tab('a', 0, { final: true }), tab('b', 1, { replaces: 'a' })]);

      expect(ids(state)).toEqual(['a', 'b']);
    });

    it('should not bring a superseded provisional tab back when it is replayed', () => {
      const state = run([provisional, tab('chapters', 2, { replaces: 'key_moments' }), provisional]);

      expect(ids(state)).toEqual(['chapters']);
    });
  });

  describe('complete.tabIds withdraws tabs', () => {
    it('should drop streamed tabs missing from complete.tabIds', () => {
      const state = run([
        tab('a', 0, { final: true }),
        tab('fallback', 1),
        tab('b', 1, { final: true }),
        { event: 'complete', tabCount: 2, tabIds: ['a', 'b'], processingTimeMs: 10 },
      ]);

      expect(ids(state)).toEqual(['a', 'b']);
    });

    it('should store the final tab order from complete', () => {
      const state = run([{ event: 'complete', tabCount: 2, tabIds: ['a', 'b'], processingTimeMs: 10 }]);

      expect(state.finalTabIds).toEqual(['a', 'b']);
    });

    it('should keep every tab when complete carries no tabIds (legacy emitter)', () => {
      const state = run([tab('a', 0), { event: 'complete', tabCount: 1, processingTimeMs: 10 }]);

      expect(ids(state)).toEqual(['a']);
    });
  });

  describe('the first assembled tab means building', () => {
    it('should advance the phase to building on the first non-provisional tab', () => {
      const state = run([{ event: 'phase', phase: 'extraction' }, tab('a', 0)]);

      expect(state.phase).toBe('building');
    });

    it('should keep the extraction phase when only the provisional tab has arrived', () => {
      const state = run([{ event: 'phase', phase: 'extraction' }, tab('moments', 2, { provisional: true })]);

      expect(state.phase).toBe('extraction');
    });

    it('should not move the phase back from translation when a tab arrives', () => {
      const state = run([{ event: 'phase', phase: 'translation' }, tab('a', 0, { final: true })]);

      expect(state.phase).toBe('translation');
    });
  });

  describe('isStreamSettled', () => {
    it('should be settled when every tab of complete.tabIds arrived final', () => {
      const state = run([
        tab('a', 0, { final: true }),
        tab('b', 1, { final: true }),
        { event: 'complete', tabCount: 2, tabIds: ['a', 'b'], processingTimeMs: 10 },
      ]);

      expect(isStreamSettled(state)).toBe(true);
    });

    it('should not be settled before complete on a live run even when every tab so far is final', () => {
      const state = run([tab('a', 0, { final: true })]);

      expect(isStreamSettled(state)).toBe(false);
    });

    it('should not be settled when complete lists a tab that never arrived', () => {
      const state = run([
        tab('a', 0, { final: true }),
        { event: 'complete', tabCount: 2, tabIds: ['a', 'b'], processingTimeMs: 10 },
      ]);

      expect(isStreamSettled(state)).toBe(false);
    });

    it('should be settled after a cached replay ends with every tab final', () => {
      const state = run([
        { event: 'cached' },
        tab('a', 0, { final: true }),
        { event: 'done', processingTimeMs: 5 },
      ]);

      expect(isStreamSettled(state)).toBe(true);
    });
  });

  describe('a full run (T1dQhQAm8Tc shape, quiz planned)', () => {
    it('should end with the saved order and nothing provisional', () => {
      const firstPass = ['overview', 'stack_overview', 'workflow_steps'].map((id, i) => tab(id, i));
      const closing = ['overview', 'stack_overview', 'workflow_steps', 'moments', 'quiz'].map((id, i) =>
        tab(id, i, { final: true }),
      );

      const state = run([
        { event: 'meta', tabCount: 4, tabLabels: [] },
        tab('moments', 3, { provisional: true }),
        ...firstPass,
        tab('moments', 3),
        ...closing,
        { event: 'complete', tabCount: 5, tabIds: closing.map((e) => e.id), processingTimeMs: 138600 },
      ]);

      expect(state.tabs.map((t) => [t.id, t.final === true, t.provisional === true])).toEqual([
        ['overview', true, false],
        ['stack_overview', true, false],
        ['workflow_steps', true, false],
        ['moments', true, false],
        ['quiz', true, false],
      ]);
    });
  });
});
