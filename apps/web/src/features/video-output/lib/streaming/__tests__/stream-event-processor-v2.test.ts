import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import type { Dispatch, SetStateAction } from 'react';
import { processEvent } from '@/features/video-output/lib/streaming/stream-event-processor';
import {
  getTelemetryCounter,
  resetTelemetryCounters,
} from '@/features/video-output/lib/telemetry';
import type { StreamState } from '@/features/video-output/hooks/use-summary-stream';

// Mock validators used by the processor
vi.mock('@/features/video-output/lib/streaming/sse-validators', () => ({
  validateMetadataEvent: vi.fn((event: Record<string, unknown>) => ({
    title: event.title,
    channel: event.channel,
    thumbnailUrl: event.thumbnailUrl,
    duration: event.duration,
  })),
  validateSynthesisComplete: vi.fn((event: Record<string, unknown>) => ({
    tldr: typeof event.tldr === 'string' ? event.tldr : '',
    keyTakeaways: Array.isArray(event.keyTakeaways) ? event.keyTakeaways : [],
  })),
  validateDoneEvent: vi.fn((event: Record<string, unknown>) => ({
    processingTimeMs: event.processingTimeMs ?? null,
    degraded: event.degraded === true,
  })),
  validateErrorEvent: vi.fn((event: Record<string, unknown>) => ({
    message: event.message ?? 'Unknown error',
    code: event.code,
  })),
  validatePhaseEvent: vi.fn((event: Record<string, unknown>) => event.phase ?? null),
}));

vi.mock('@/features/video-output/lib/streaming/stream-error-messages', () => ({
  getUserFriendlyError: vi.fn((msg: string) => msg),
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

/**
 * Creates a mock setState that captures the updater function and applies it
 * to the initial state, returning the resulting state.
 */
function createMockSetState() {
  let capturedState = { ...initialState };
  const setState: Dispatch<SetStateAction<StreamState>> = (action) => {
    if (typeof action === 'function') {
      capturedState = action(capturedState);
    } else {
      capturedState = action;
    }
  };
  return {
    setState,
    getState: () => capturedState,
  };
}

describe('stream-event-processor — Pipeline events', () => {
  let mockSetState: ReturnType<typeof createMockSetState>;

  beforeEach(() => {
    vi.clearAllMocks();
    mockSetState = createMockSetState();
  });

  // ─────────────────────────────────────────────────────
  // triage_complete
  // ─────────────────────────────────────────────────────

  describe('triage_complete', () => {
    it('should update triage and set phase to "extraction"', () => {
      const event = {
        event: 'triage_complete',
        contentTags: ['food'],
        modifiers: [],
        primaryTag: 'food',
        confidence: 0.95,
        userGoal: 'Learn to cook pasta',
        tabs: [
          { id: 'ingredients', label: 'Ingredients', emoji: '🧅', dataSource: 'food' },
          { id: 'steps', label: 'Steps', emoji: '👨‍🍳', dataSource: 'food' },
        ],
      };

      processEvent(event, mockSetState.setState);

      const state = mockSetState.getState();
      expect(state.phase).toBe('extraction');
      expect(state.triage).toEqual({
        contentTags: ['food'],
        modifiers: [],
        primaryTag: 'food',
        confidence: 0.95,
        userGoal: 'Learn to cook pasta',
        tabs: [
          { id: 'ingredients', label: 'Ingredients', emoji: '🧅', dataSource: 'food' },
          { id: 'steps', label: 'Steps', emoji: '👨‍🍳', dataSource: 'food' },
        ],
      });
    });

    it('should default primaryTag to "learning" when missing', () => {
      processEvent(
        { event: 'triage_complete', confidence: 0.5, contentTags: [] },
        mockSetState.setState,
      );
      expect(mockSetState.getState().triage?.primaryTag).toBe('learning');
    });

    it('should default confidence to 0 when missing', () => {
      processEvent(
        { event: 'triage_complete', contentTags: ['tech'], primaryTag: 'tech' },
        mockSetState.setState,
      );
      expect(mockSetState.getState().triage?.confidence).toBe(0);
    });

    it('should default userGoal to empty string when missing', () => {
      processEvent(
        { event: 'triage_complete', contentTags: ['tech'], primaryTag: 'tech', confidence: 0.8 },
        mockSetState.setState,
      );
      expect(mockSetState.getState().triage?.userGoal).toBe('');
    });

    it('should default tabs to empty array when missing', () => {
      processEvent(
        { event: 'triage_complete', contentTags: ['review'], primaryTag: 'review', confidence: 0.9, userGoal: 'Review the product' },
        mockSetState.setState,
      );
      expect(mockSetState.getState().triage?.tabs).toEqual([]);
    });

    it('should handle legacy intent_detected event as triage_complete', () => {
      processEvent(
        { event: 'intent_detected', contentTags: ['learning'], primaryTag: 'learning', confidence: 0.8 },
        mockSetState.setState,
      );
      expect(mockSetState.getState().triage?.primaryTag).toBe('learning');
      expect(mockSetState.getState().phase).toBe('extraction');
    });
  });

  // ─────────────────────────────────────────────────────
  // extraction_progress
  // ─────────────────────────────────────────────────────

  describe('extraction_progress', () => {
    it('should update extractionProgress with section and percent', () => {
      processEvent(
        { event: 'extraction_progress', section: 'ingredients', percent: 45 },
        mockSetState.setState,
      );

      const state = mockSetState.getState();
      expect(state.phase).toBe('extraction');
      expect(state.extractionProgress).toEqual({ section: 'ingredients', percent: 45 });
    });

    it('should default section to empty string when missing', () => {
      processEvent(
        { event: 'extraction_progress', percent: 75 },
        mockSetState.setState,
      );
      expect(mockSetState.getState().extractionProgress?.section).toBe('');
    });

    it('should default percent to 0 when missing', () => {
      processEvent(
        { event: 'extraction_progress', section: 'steps' },
        mockSetState.setState,
      );
      expect(mockSetState.getState().extractionProgress?.percent).toBe(0);
    });

    it('should overwrite previous extraction progress', () => {
      processEvent(
        { event: 'extraction_progress', section: 'intro', percent: 20 },
        mockSetState.setState,
      );
      processEvent(
        { event: 'extraction_progress', section: 'main', percent: 80 },
        mockSetState.setState,
      );

      expect(mockSetState.getState().extractionProgress).toEqual({ section: 'main', percent: 80 });
    });

    // Phase 3 — chunked extraction emits batch/of fields per completed batch.
    it('should parse batch and of fields when present', () => {
      processEvent(
        { event: 'extraction_progress', section: 'chunked', percent: 30, batch: 2, of: 4 },
        mockSetState.setState,
      );

      const state = mockSetState.getState();
      expect(state.extractionProgress?.batch).toBe(2);
      expect(state.extractionProgress?.of).toBe(4);
      expect(state.extractionProgress?.section).toBe('chunked');
    });

    it('should preserve chunked-sequential section name verbatim', () => {
      processEvent(
        { event: 'extraction_progress', section: 'chunked-sequential', percent: 70, batch: 3, of: 4 },
        mockSetState.setState,
      );

      expect(mockSetState.getState().extractionProgress?.section).toBe('chunked-sequential');
    });

    it('should leave batch/of undefined for legacy events', () => {
      processEvent(
        { event: 'extraction_progress', section: 'all', percent: 50 },
        mockSetState.setState,
      );

      const state = mockSetState.getState();
      expect(state.extractionProgress?.batch).toBeUndefined();
      expect(state.extractionProgress?.of).toBeUndefined();
    });

    it('should ignore non-numeric batch/of fields', () => {
      processEvent(
        { event: 'extraction_progress', section: 'chunked', percent: 30, batch: '2', of: 'four' },
        mockSetState.setState,
      );

      const state = mockSetState.getState();
      expect(state.extractionProgress?.batch).toBeUndefined();
      expect(state.extractionProgress?.of).toBeUndefined();
    });
  });

  // ─────────────────────────────────────────────────────
  // extraction_complete
  // ─────────────────────────────────────────────────────

  describe('extraction_complete', () => {
    it('should merge domain-keyed data into domainData', () => {
      const learningData = {
        keyPoints: [{ emoji: '1', title: 'Point 1', detail: 'Detail 1' }],
        concepts: [],
        takeaways: ['Takeaway 1'],
        timestamps: [],
      };

      processEvent(
        { event: 'extraction_complete', data: { learning: learningData } },
        mockSetState.setState,
      );

      expect(mockSetState.getState().domainData).toEqual({ learning: learningData });
    });

    it('should handle empty data gracefully', () => {
      processEvent(
        { event: 'extraction_complete', data: {} },
        mockSetState.setState,
      );
      expect(mockSetState.getState().domainData).toEqual({});
    });

    it('should merge multiple domain data entries', () => {
      const foodData = {
        meta: { prepTime: 15, cookTime: 30, servings: 4 },
        ingredients: [{ name: 'Pasta', amount: '500', unit: 'g' }],
        steps: [{ number: 1, instruction: 'Boil water' }],
        tips: [],
        substitutions: [],
        nutrition: [],
        equipment: ['Pot'],
      };

      processEvent(
        { event: 'extraction_complete', data: { food: foodData } },
        mockSetState.setState,
      );

      expect(mockSetState.getState().domainData).toEqual({ food: foodData });
    });

    it('should merge with existing domainData from previous extraction events', () => {
      // First extraction
      processEvent(
        { event: 'extraction_complete', data: { learning: { keyPoints: [] } } },
        mockSetState.setState,
      );
      // Second extraction
      processEvent(
        { event: 'extraction_complete', data: { tech: { code: [] } } },
        mockSetState.setState,
      );

      const domainData = mockSetState.getState().domainData;
      expect(domainData).toEqual({ learning: { keyPoints: [] }, tech: { code: [] } });
    });
  });

  // ─────────────────────────────────────────────────────
  // enrichment_complete
  // ─────────────────────────────────────────────────────

  describe('enrichment_complete', () => {
    it('should update enrichment with quiz data', () => {
      const quiz = [
        { question: 'What is React?', options: ['Library', 'Framework', 'Language', 'Database'], correctIndex: 0, explanation: 'React is a JavaScript library' },
      ];

      processEvent({ event: 'enrichment_complete', quiz }, mockSetState.setState);
      expect(mockSetState.getState().enrichment).toEqual({ quiz });
    });

    it('should update enrichment with flashcards data', () => {
      const flashcards = [{ front: 'What is JSX?', back: 'A syntax extension for JavaScript' }];

      processEvent({ event: 'enrichment_complete', flashcards }, mockSetState.setState);
      expect(mockSetState.getState().enrichment).toEqual({ flashcards });
    });

    it('should update enrichment with cheatSheet data', () => {
      const cheatSheet = [{ title: 'useState', code: 'const [x, setX] = useState(0)', description: 'State hook' }];

      processEvent({ event: 'enrichment_complete', cheatSheet }, mockSetState.setState);
      expect(mockSetState.getState().enrichment).toEqual({ cheatSheet });
    });

    it('should handle all enrichment fields together', () => {
      const quiz = [{ question: 'Q?', options: ['A', 'B'], correctIndex: 0, explanation: 'E' }];
      const flashcards = [{ front: 'F', back: 'B' }];
      const cheatSheet = [{ title: 'T', code: 'C', description: 'D' }];

      processEvent({ event: 'enrichment_complete', quiz, flashcards, cheatSheet }, mockSetState.setState);
      expect(mockSetState.getState().enrichment).toEqual({ quiz, flashcards, cheatSheet });
    });

    it('should produce empty enrichment when no arrays are provided', () => {
      processEvent({ event: 'enrichment_complete' }, mockSetState.setState);
      expect(mockSetState.getState().enrichment).toEqual({});
    });

    it('should ignore non-array quiz field', () => {
      processEvent(
        { event: 'enrichment_complete', quiz: 'not an array', flashcards: [{ front: 'A', back: 'B' }] },
        mockSetState.setState,
      );

      const state = mockSetState.getState();
      expect(state.enrichment?.quiz).toBeUndefined();
      expect(state.enrichment?.flashcards).toHaveLength(1);
    });
  });

  // ─────────────────────────────────────────────────────
  // done
  // ─────────────────────────────────────────────────────

  describe('done', () => {
    it('should set phase to done and increment confettiCount', () => {
      processEvent(
        { event: 'done', processingTimeMs: 5000 },
        mockSetState.setState,
      );

      const state = mockSetState.getState();
      expect(state.phase).toBe('done');
      expect(state.confettiCount).toBe(1);
    });

    it('should not increment confettiCount when cached', () => {
      // Set isCached first
      processEvent({ event: 'cached' }, mockSetState.setState);
      processEvent({ event: 'done', processingTimeMs: 100 }, mockSetState.setState);

      expect(mockSetState.getState().confettiCount).toBe(0);
    });

    it('should surface degraded from the done event', () => {
      processEvent(
        { event: 'done', processingTimeMs: 5000, degraded: true },
        mockSetState.setState,
      );

      expect(mockSetState.getState().degraded).toBe(true);
    });

    it('should keep degraded false when the done event omits it', () => {
      processEvent({ event: 'done', processingTimeMs: 5000 }, mockSetState.setState);

      expect(mockSetState.getState().degraded).toBe(false);
    });

    it('should keep degraded sticky when complete flagged it and done does not', () => {
      processEvent(
        { event: 'complete', tabCount: 3, processingTimeMs: 100, degraded: true },
        mockSetState.setState,
      );
      processEvent({ event: 'done', processingTimeMs: 100 }, mockSetState.setState);

      expect(mockSetState.getState().degraded).toBe(true);
    });
  });

  // ─────────────────────────────────────────────────────
  // error
  // ─────────────────────────────────────────────────────

  describe('error', () => {
    it('should set phase to error with message', () => {
      processEvent(
        { event: 'error', message: 'Something went wrong' },
        mockSetState.setState,
      );

      const state = mockSetState.getState();
      expect(state.phase).toBe('error');
      expect(state.error).toBe('Something went wrong');
    });
  });

  // ─────────────────────────────────────────────────────
  // frames
  // ─────────────────────────────────────────────────────

  describe('frames', () => {
    it('should parse and store frame data', () => {
      processEvent(
        {
          event: 'frames',
          frames: [
            { index: 0, timestamp: 10.5, url: 'https://example.com/frame0.jpg', s3Key: 'frames/0.jpg', ocrText: 'Hello', textDensity: 0.3 },
            { index: 1, timestamp: 25.0, url: 'https://example.com/frame1.jpg' },
          ],
        },
        mockSetState.setState,
      );

      const state = mockSetState.getState();
      expect(state.frames).toHaveLength(2);
      expect(state.frames[0]).toEqual({
        index: 0,
        timestamp: 10.5,
        url: 'https://example.com/frame0.jpg',
        s3Key: 'frames/0.jpg',
        ocrText: 'Hello',
        textDensity: 0.3,
      });
      expect(state.frames[1]).toEqual({
        index: 1,
        timestamp: 25.0,
        url: 'https://example.com/frame1.jpg',
        s3Key: undefined,
        ocrText: undefined,
        textDensity: undefined,
      });
    });

    it('should default to empty array when frames is not an array', () => {
      processEvent(
        { event: 'frames', frames: 'not-an-array' },
        mockSetState.setState,
      );

      expect(mockSetState.getState().frames).toEqual([]);
    });

    it('should handle missing fields with safe defaults', () => {
      processEvent(
        { event: 'frames', frames: [{}] },
        mockSetState.setState,
      );

      expect(mockSetState.getState().frames[0]).toEqual({
        index: 0,
        timestamp: 0,
        url: '',
        s3Key: undefined,
        ocrText: undefined,
        textDensity: undefined,
      });
    });
  });

  // ─────────────────────────────────────────────────────
  // warning
  // ─────────────────────────────────────────────────────

  describe('warning', () => {
    it('should append warning to warnings array', () => {
      processEvent(
        { event: 'warning', message: 'Partial failure', failedTasks: ['enrichment'] },
        mockSetState.setState,
      );

      expect(mockSetState.getState().warnings).toEqual(['Partial failure (failed: enrichment)']);
    });
  });

  // ─────────────────────────────────────────────────────
  // v2: Assembly Pipeline Events
  // ─────────────────────────────────────────────────────

  describe('meta (v2)', () => {
    it('should update meta, tabCount, tabLabels and reset tabs', () => {
      processEvent(
        {
          event: 'meta',
          title: 'Test Video',
          contentTags: ['food'],
          modifiers: [],
          primaryTag: 'food',
          tabCount: 3,
          tabLabels: [
            { id: 'ingredients', label: 'Ingredients', emoji: '🧅' },
            { id: 'steps', label: 'Steps', emoji: '👨‍🍳' },
            { id: 'tips', label: 'Tips', emoji: '💡' },
          ],
        },
        mockSetState.setState,
      );

      const state = mockSetState.getState();
      expect(state.meta).toEqual({
        videoId: '',
        videoTitle: 'Test Video',
        creator: '',
        contentTags: ['food'],
        modifiers: [],
        primaryTag: 'food',
        userGoal: '',
      });
      expect(state.tabs).toEqual([]);
      expect(state.tabCount).toBe(3);
      expect(state.tabLabels).toHaveLength(3);
      expect(state.tabLabels[0]).toEqual({ id: 'ingredients', label: 'Ingredients', emoji: '🧅' });
    });

    it('should default missing fields to empty values', () => {
      processEvent({ event: 'meta' }, mockSetState.setState);

      const state = mockSetState.getState();
      expect(state.meta?.videoTitle).toBe('');
      expect(state.meta?.contentTags).toEqual([]);
      expect(state.meta?.primaryTag).toBe('learning');
      expect(state.tabCount).toBe(0);
      expect(state.tabLabels).toEqual([]);
    });

    it('should carry the degraded flag onto meta (cached degraded serves)', () => {
      processEvent({ event: 'meta', degraded: true }, mockSetState.setState);

      expect(mockSetState.getState().meta?.degraded).toBe(true);
    });

    it('should leave meta.degraded unset when the event omits it', () => {
      processEvent({ event: 'meta' }, mockSetState.setState);

      expect(mockSetState.getState().meta?.degraded).toBeUndefined();
    });
  });

  describe('tab_ready (v2)', () => {
    it('should append assembled tab', () => {
      processEvent(
        {
          event: 'tab_ready',
          id: 'ingredients',
          label: 'Ingredients',
          emoji: '🧅',
          component: 'checklist',
          props: { items: [{ label: 'Pasta' }], tabLabel: 'Ingredients' },
          crossTabLinks: [{ targetTab: 'steps', label: 'Go to steps' }],
        },
        mockSetState.setState,
      );

      const tabs = mockSetState.getState().tabs;
      expect(tabs).toHaveLength(1);
      expect(tabs[0].component).toBe('checklist');
      expect(tabs[0].props).toEqual({ items: [{ label: 'Pasta' }], tabLabel: 'Ingredients' });
      expect(tabs[0].crossTabLinks).toEqual([{ targetTab: 'steps', label: 'Go to steps' }]);
    });

    it('should accumulate multiple tabs', () => {
      processEvent(
        { event: 'tab_ready', id: 'tab1', label: 'Tab 1', emoji: '1️⃣', component: 'display_section', props: {} },
        mockSetState.setState,
      );
      processEvent(
        { event: 'tab_ready', id: 'tab2', label: 'Tab 2', emoji: '2️⃣', component: 'display_section', props: {} },
        mockSetState.setState,
      );

      expect(mockSetState.getState().tabs).toHaveLength(2);
    });

    it('should default missing props to empty object', () => {
      processEvent(
        { event: 'tab_ready', id: 'overview', label: 'Overview', emoji: '📋', component: 'overview' },
        mockSetState.setState,
      );

      expect(mockSetState.getState().tabs[0].props).toEqual({});
    });

    it('should splice a late tab into its persisted position', () => {
      // The backend holds moment_track back until its frame fill completes and
      // streams it last with position = its index in the persisted order.
      const base = { event: 'tab_ready', emoji: '•', component: 'display_section', props: {} };
      processEvent({ ...base, id: 'overview', label: 'Overview', position: 0 }, mockSetState.setState);
      processEvent({ ...base, id: 'facts', label: 'Facts', position: 2 }, mockSetState.setState);
      processEvent({ ...base, id: 'moments', label: 'Moments', position: 1 }, mockSetState.setState);

      expect(mockSetState.getState().tabs.map((t) => t.id)).toEqual(['overview', 'moments', 'facts']);
    });

    it('should append when position is missing or invalid', () => {
      const base = { event: 'tab_ready', emoji: '•', component: 'display_section', props: {} };
      processEvent({ ...base, id: 'a', label: 'A' }, mockSetState.setState);
      processEvent({ ...base, id: 'b', label: 'B', position: -1 }, mockSetState.setState);
      processEvent({ ...base, id: 'c', label: 'C', position: 'first' }, mockSetState.setState);
      processEvent({ ...base, id: 'd', label: 'D', position: 99 }, mockSetState.setState);

      expect(mockSetState.getState().tabs.map((t) => t.id)).toEqual(['a', 'b', 'c', 'd']);
    });

    it('should replace in place (not move) when a positioned tab re-arrives', () => {
      const base = { event: 'tab_ready', emoji: '•', component: 'display_section', props: {} };
      processEvent({ ...base, id: 'a', label: 'A', position: 0 }, mockSetState.setState);
      processEvent({ ...base, id: 'b', label: 'B', position: 1 }, mockSetState.setState);
      processEvent({ ...base, id: 'a', label: 'A2', position: 1 }, mockSetState.setState);

      const tabs = mockSetState.getState().tabs;
      expect(tabs.map((t) => t.id)).toEqual(['a', 'b']);
      expect(tabs[0].label).toBe('A2');
    });
  });

  // ─────────────────────────────────────────────────────
  // tab_ready prop validation at the boundary (4.4)
  // ─────────────────────────────────────────────────────

  describe('tab_ready prop validation (v2, project-score-9 4.4)', () => {
    let warnSpy: ReturnType<typeof vi.spyOn>;

    beforeEach(() => {
      resetTelemetryCounters();
      warnSpy = vi.spyOn(console, 'warn').mockImplementation(() => {});
    });

    afterEach(() => {
      warnSpy.mockRestore();
      resetTelemetryCounters();
    });

    it('should remap a tab with malformed props to display_section and increment the drift counter', () => {
      const malformedProps = { items: 'Pasta, Oil, Garlic' }; // string masquerading as list
      processEvent(
        { event: 'tab_ready', id: 'ingredients', label: 'Ingredients', emoji: '🧅', component: 'checklist', props: malformedProps },
        mockSetState.setState,
      );

      const tab = mockSetState.getState().tabs[0];
      expect(tab.component).toBe('display_section');
      // Raw payload is preserved for the fallback renderer — never dropped.
      expect(tab.props).toEqual({ data: malformedProps });
      expect(getTelemetryCounter('tab_props_invalid')).toBe(1);
      expect(getTelemetryCounter('tab_props_invalid.checklist')).toBe(1);
      expect(warnSpy).toHaveBeenCalled();
    });

    it('should remap a quiz tab whose correctIndex is out of bounds', () => {
      processEvent(
        {
          event: 'tab_ready',
          id: 'quiz',
          label: 'Quiz',
          emoji: '🧪',
          component: 'quiz_arena',
          props: { questions: [{ question: 'Q?', options: ['A', 'B'], correctIndex: 5 }] },
        },
        mockSetState.setState,
      );

      expect(mockSetState.getState().tabs[0].component).toBe('display_section');
      expect(getTelemetryCounter('tab_props_invalid.quiz_arena')).toBe(1);
    });

    it('should remap a tab with an unknown component and count it separately', () => {
      processEvent(
        { event: 'tab_ready', id: 'mystery', label: 'Mystery', emoji: '❓', component: 'does_not_exist', props: { foo: 1 } },
        mockSetState.setState,
      );

      const tab = mockSetState.getState().tabs[0];
      expect(tab.component).toBe('display_section');
      expect(tab.props).toEqual({ data: { foo: 1 } });
      expect(getTelemetryCounter('tab_component_unknown')).toBe(1);
      expect(getTelemetryCounter('tab_props_invalid')).toBe(0);
    });

    it('should leave a valid tab untouched and not increment any counter', () => {
      const props = { items: [{ label: 'Pasta', note: null }], scalable: true };
      processEvent(
        { event: 'tab_ready', id: 'ingredients', label: 'Ingredients', emoji: '🧅', component: 'checklist', props },
        mockSetState.setState,
      );

      const tab = mockSetState.getState().tabs[0];
      expect(tab.component).toBe('checklist');
      expect(tab.props).toEqual(props);
      expect(getTelemetryCounter('tab_props_invalid')).toBe(0);
      expect(getTelemetryCounter('tab_component_unknown')).toBe(0);
      expect(warnSpy).not.toHaveBeenCalled();
    });

    it('should tolerate assembler fields the frontend does not know yet', () => {
      processEvent(
        {
          event: 'tab_ready',
          id: 'steps',
          label: 'Steps',
          emoji: '👣',
          component: 'step_player',
          props: { steps: [{ number: 1, instruction: 'Cut', frameCaption: 'Saw on plank' }], futureTopLevel: true },
        },
        mockSetState.setState,
      );

      expect(mockSetState.getState().tabs[0].component).toBe('step_player');
      expect(getTelemetryCounter('tab_props_invalid')).toBe(0);
    });
  });

  describe('complete (v2)', () => {
    it('should store processingTimeMs without setting phase (done event handles that)', () => {
      processEvent(
        { event: 'complete', tabCount: 4, processingTimeMs: 3500 },
        mockSetState.setState,
      );

      const state = mockSetState.getState();
      expect(state.processingTimeMs).toBe(3500);
      // complete does NOT set phase or confetti — done event handles that
      expect(state.confettiCount).toBe(0);
    });

    it('should not affect confettiCount when cached', () => {
      processEvent({ event: 'cached' }, mockSetState.setState);
      processEvent({ event: 'complete', tabCount: 3, processingTimeMs: 100 }, mockSetState.setState);

      expect(mockSetState.getState().confettiCount).toBe(0);
    });

    it('should surface degraded from the complete event', () => {
      processEvent(
        { event: 'complete', tabCount: 4, processingTimeMs: 3500, degraded: true },
        mockSetState.setState,
      );

      expect(mockSetState.getState().degraded).toBe(true);
    });

    it('should keep degraded false when the complete event omits it', () => {
      processEvent(
        { event: 'complete', tabCount: 4, processingTimeMs: 3500 },
        mockSetState.setState,
      );

      expect(mockSetState.getState().degraded).toBe(false);
    });
  });

  // ─────────────────────────────────────────────────────
  // phase mapping (raw SSE phase → UI phase + detail)
  // ─────────────────────────────────────────────────────

  describe('phase mapping', () => {
    it.each([
      ['metadata', 'metadata', null],
      ['transcript', 'transcript', null],
      ['transcript_cached', 'transcript', 'captions-cached'],
      ['audio_transcription', 'transcript', 'audio-transcription'],
      ['whisper_transcription', 'transcript', 'audio-transcription'],
      ['metadata_fallback', 'transcript', 'metadata-only'],
      ['triage', 'extraction', null],
      ['extraction', 'extraction', null],
      ['enrichment', 'building', null],
      ['synthesis', 'building', null],
      ['translation', 'translation', null],
    ])('should map raw phase "%s" to UI phase "%s"', (raw, expectedPhase, expectedDetail) => {
      processEvent({ event: 'phase', phase: raw }, mockSetState.setState);

      const state = mockSetState.getState();
      expect(state.phase).toBe(expectedPhase);
      expect(state.phaseDetail).toBe(expectedDetail);
    });

    it('should ignore unknown raw phases without changing state', () => {
      processEvent({ event: 'phase', phase: 'metadata' }, mockSetState.setState);
      processEvent({ event: 'phase', phase: 'not_a_phase' }, mockSetState.setState);

      expect(mockSetState.getState().phase).toBe('metadata');
    });

    it('should clear phaseDetail when a later stage begins', () => {
      processEvent({ event: 'phase', phase: 'whisper_transcription' }, mockSetState.setState);
      processEvent(
        { event: 'triage_complete', contentTags: ['tech'], primaryTag: 'tech', confidence: 0.9 },
        mockSetState.setState,
      );

      const state = mockSetState.getState();
      expect(state.phase).toBe('extraction');
      expect(state.phaseDetail).toBeNull();
    });
  });

  describe('meta leaves the phase alone', () => {
    it('should NOT advance the phase (meta fires at plan time, not assembly)', () => {
      processEvent({ event: 'phase', phase: 'extraction' }, mockSetState.setState);
      processEvent({ event: 'meta', tabCount: 4, tabLabels: [] }, mockSetState.setState);

      expect(mockSetState.getState().phase).toBe('extraction');
    });
  });

  describe('complete updates tabCount to the assembled count', () => {
    it('should replace the plan-time tabCount with the assembled count', () => {
      processEvent({ event: 'meta', tabCount: 6, tabLabels: [] }, mockSetState.setState);
      processEvent(
        { event: 'complete', tabCount: 4, processingTimeMs: 100 },
        mockSetState.setState,
      );

      expect(mockSetState.getState().tabCount).toBe(4);
    });

    it('should keep the prior tabCount when complete omits it', () => {
      processEvent({ event: 'meta', tabCount: 6, tabLabels: [] }, mockSetState.setState);
      processEvent({ event: 'complete', processingTimeMs: 100 }, mockSetState.setState);

      expect(mockSetState.getState().tabCount).toBe(6);
    });
  });

  describe('id-less tabs do not collapse', () => {
    it('should append two id-less tab_ready events as two tabs', () => {
      processEvent(
        { event: 'tab_ready', label: 'First', component: 'display_section', props: {} },
        mockSetState.setState,
      );
      processEvent(
        { event: 'tab_ready', label: 'Second', component: 'display_section', props: {} },
        mockSetState.setState,
      );

      expect(mockSetState.getState().tabs).toHaveLength(2);
    });

    it('should still replace a re-sent tab with the same real id', () => {
      processEvent(
        { event: 'tab_ready', id: 'a', label: 'v1', component: 'display_section', props: {} },
        mockSetState.setState,
      );
      processEvent(
        { event: 'tab_ready', id: 'a', label: 'v2', component: 'display_section', props: {} },
        mockSetState.setState,
      );

      const tabs = mockSetState.getState().tabs;
      expect(tabs).toHaveLength(1);
      expect(tabs[0].label).toBe('v2');
    });
  });

  describe('transcript_ready', () => {
    it('should store the duration from the event', () => {
      processEvent({ event: 'transcript_ready', duration: 5400 }, mockSetState.setState);

      expect(mockSetState.getState().duration).toBe(5400);
    });

    it('should keep the existing duration when the event omits it', () => {
      processEvent(
        { event: 'metadata', title: 'T', duration: 600 },
        mockSetState.setState,
      );
      processEvent({ event: 'transcript_ready' }, mockSetState.setState);

      expect(mockSetState.getState().duration).toBe(600);
    });
  });
});
