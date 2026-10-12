import { describe, it, expect, vi, beforeEach } from 'vitest';
import {
  validateDescriptionAnalysis,
  validateMetadataEvent,
  validateSynthesisComplete,
  validateDoneEvent,
  validateErrorEvent,
  validatePhaseEvent,
  validateTabReadyEvent,
} from '../sse-validators';

// Mock the sse-logger to avoid console output during tests
vi.mock('../sse-logger', () => ({
  sseLogger: {
    warn: vi.fn(),
    error: vi.fn(),
    info: vi.fn(),
  },
}));

describe('sse-validators', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  // ─────────────────────────────────────────────────────
  // validateDescriptionAnalysis Tests
  // ─────────────────────────────────────────────────────

  describe('validateDescriptionAnalysis', () => {
    it('should validate complete description analysis', () => {
      const data = {
        links: [{ url: 'https://example.com', type: 'documentation', label: 'Docs' }],
        resources: [{ name: 'Resource', url: 'https://resource.com' }],
        relatedVideos: [{ title: 'Related', url: 'https://youtube.com/watch?v=test' }],
        socialLinks: [{ platform: 'twitter', url: 'https://twitter.com/test' }],
      };

      const result = validateDescriptionAnalysis(data);

      expect(result).toEqual(data);
    });

    it('should use defaults for missing arrays', () => {
      const result = validateDescriptionAnalysis({});

      expect(result).toEqual({
        links: [],
        resources: [],
        relatedVideos: [],
        socialLinks: [],
      });
    });

    it('should return null for invalid input', () => {
      const result = validateDescriptionAnalysis('invalid');

      expect(result).toBeNull();
    });

    it('should return null when arrays contain invalid items', () => {
      const result = validateDescriptionAnalysis({
        links: [{ invalid: 'data' }], // Missing required fields
      });

      expect(result).toBeNull();
    });
  });

  // ─────────────────────────────────────────────────────
  // validateMetadataEvent Tests
  // ─────────────────────────────────────────────────────

  describe('validateMetadataEvent', () => {
    it('should validate complete metadata event', () => {
      const data = {
        event: 'metadata',
        title: 'Test Video',
        channel: 'Test Channel',
        thumbnailUrl: 'https://example.com/thumb.jpg',
        duration: 3600,
        context: {
          category: 'coding',
          youtubeCategory: 'Education',
          tags: ['programming'],
          displayTags: ['Programming'],
        },
      };

      const result = validateMetadataEvent(data);

      expect(result).toEqual({
        title: 'Test Video',
        channel: 'Test Channel',
        thumbnailUrl: 'https://example.com/thumb.jpg',
        duration: 3600,
        context: {
          category: 'coding',
          youtubeCategory: 'Education',
          tags: ['programming'],
          displayTags: ['Programming'],
        },
      });
    });

    it('should handle partial metadata', () => {
      const data = {
        event: 'metadata',
        title: 'Just Title',
      };

      const result = validateMetadataEvent(data);

      expect(result).toEqual({
        title: 'Just Title',
        channel: undefined,
        thumbnailUrl: undefined,
        duration: undefined,
        context: undefined,
      });
    });

    it('should return empty object for invalid event', () => {
      const result = validateMetadataEvent({ event: 'wrong' });

      expect(result).toEqual({});
    });

    it('should return empty object for invalid context category', () => {
      const data = {
        event: 'metadata',
        title: 'Test',
        context: {
          // Missing required 'category' field
          youtubeCategory: 'Education',
          tags: [],
          displayTags: [],
        },
      };

      const result = validateMetadataEvent(data);

      expect(result).toEqual({});
    });
  });

  // ─────────────────────────────────────────────────────
  // validateSynthesisComplete Tests
  // ─────────────────────────────────────────────────────

  describe('validateSynthesisComplete', () => {
    it('should validate complete synthesis event', () => {
      const data = {
        event: 'synthesis_complete',
        tldr: 'This is the summary',
        keyTakeaways: ['Point 1', 'Point 2', 'Point 3'],
      };

      const result = validateSynthesisComplete(data);

      expect(result).toEqual({
        tldr: 'This is the summary',
        keyTakeaways: ['Point 1', 'Point 2', 'Point 3'],
      });
    });

    it('should keep masterSummary and seoDescription when the superset arrives', () => {
      const data = {
        event: 'synthesis_complete',
        tldr: 'Final tldr',
        keyTakeaways: [],
        masterSummary: 'The full master summary.',
        seoDescription: 'SEO description.',
      };

      const result = validateSynthesisComplete(data);

      expect(result).toEqual({
        tldr: 'Final tldr',
        masterSummary: 'The full master summary.',
        seoDescription: 'SEO description.',
      });
    });

    it('should return no fields when the event carries no content', () => {
      const result = validateSynthesisComplete({ event: 'synthesis_complete', tldr: '' });

      expect(result).toEqual({});
    });

    it('should return no fields for an invalid event', () => {
      const result = validateSynthesisComplete({ event: 'wrong' });

      expect(result).toEqual({});
    });
  });

  // ─────────────────────────────────────────────────────
  // validateDoneEvent Tests
  // ─────────────────────────────────────────────────────

  describe('validateDoneEvent', () => {
    it('should validate done event with processing time', () => {
      const data = { event: 'done', processingTimeMs: 5000 };

      const result = validateDoneEvent(data);

      expect(result).toEqual({ processingTimeMs: 5000, degraded: false });
    });

    it('should return null processing time when processingTimeMs is null', () => {
      const data = { event: 'done', processingTimeMs: null };

      const result = validateDoneEvent(data);

      expect(result.processingTimeMs).toBeNull();
    });

    it('should return null processing time when processingTimeMs is undefined', () => {
      const data = { event: 'done' };

      const result = validateDoneEvent(data);

      expect(result.processingTimeMs).toBeNull();
    });

    it('should surface degraded true when the terminal event carries it', () => {
      const data = { event: 'done', processingTimeMs: 5000, degraded: true };

      const result = validateDoneEvent(data);

      expect(result.degraded).toBe(true);
    });

    it('should default degraded to false when absent (legacy events)', () => {
      const result = validateDoneEvent({ event: 'done', processingTimeMs: 100 });

      expect(result.degraded).toBe(false);
    });

    it('should return defaults for invalid event', () => {
      const result = validateDoneEvent({ event: 'wrong' });

      expect(result).toEqual({ processingTimeMs: null, degraded: false });
    });
  });

  // ─────────────────────────────────────────────────────
  // validateErrorEvent Tests
  // ─────────────────────────────────────────────────────

  describe('validateErrorEvent', () => {
    it('should validate error event with message and code', () => {
      const data = {
        event: 'error',
        message: 'Something went wrong',
        code: 'ERR_001',
      };

      const result = validateErrorEvent(data);

      expect(result).toEqual({
        message: 'Something went wrong',
        code: 'ERR_001',
      });
    });

    it('should use default message when missing', () => {
      const data = { event: 'error' };

      const result = validateErrorEvent(data);

      expect(result).toEqual({
        message: 'Unknown error',
        code: undefined,
      });
    });

    it('should return default for invalid event', () => {
      const result = validateErrorEvent({ event: 'wrong' });

      expect(result).toEqual({ message: 'Unknown error' });
    });
  });

  // ─────────────────────────────────────────────────────
  // validatePhaseEvent Tests
  // ─────────────────────────────────────────────────────

  describe('validatePhaseEvent', () => {
    it('should validate all valid phases', () => {
      // Mirror of every phase the summarizer emits (transcript_fetcher.py +
      // translation.py) plus the legacy pipeline names.
      const validPhases = [
        'metadata',
        'metadata_fallback',
        'transcript',
        'transcript_cached',
        'audio_transcription',
        'whisper_transcription',
        'triage',
        'extraction',
        'enrichment',
        'synthesis',
        'translation',
      ];

      for (const phase of validPhases) {
        const result = validatePhaseEvent({ event: 'phase', phase });
        expect(result).toBe(phase);
      }
    });

    it('should return null for invalid phase', () => {
      const result = validatePhaseEvent({ event: 'phase', phase: 'invalid_phase' });

      expect(result).toBeNull();
    });

    it('should return null for missing phase', () => {
      const result = validatePhaseEvent({ event: 'phase' });

      expect(result).toBeNull();
    });

    it('should return null for wrong event type', () => {
      const result = validatePhaseEvent({ event: 'wrong', phase: 'metadata' });

      expect(result).toBeNull();
    });
  });

  // ─────────────────────────────────────────────────────
  // validateTabReadyEvent Tests
  // ─────────────────────────────────────────────────────

  describe('validateTabReadyEvent', () => {
    const base = { event: 'tab_ready', id: 'moments', label: 'Moments', emoji: '🎬', component: 'moment_track', props: {} };

    it('should keep the stream fields when they are well-formed', () => {
      const result = validateTabReadyEvent({ ...base, position: 2, final: true, replaces: 'key_moments' });

      expect(result).toEqual(expect.objectContaining({ position: 2, final: true, replaces: 'key_moments' }));
    });

    it('should keep the provisional flag', () => {
      const result = validateTabReadyEvent({ ...base, provisional: true });

      expect(result.provisional).toBe(true);
    });

    it('should drop a negative or fractional position instead of the tab', () => {
      const result = validateTabReadyEvent({ ...base, position: 1.5 });

      expect(result).toEqual(expect.objectContaining({ id: 'moments', position: undefined }));
    });

    it('should drop an empty replaces id', () => {
      const result = validateTabReadyEvent({ ...base, replaces: '' });

      expect(result.replaces).toBeUndefined();
    });

    it('should default non-object props to an empty object', () => {
      const result = validateTabReadyEvent({ ...base, props: ['not', 'a', 'record'] });

      expect(result.props).toEqual({});
    });

    it('should drop malformed cross-tab links and attachments but keep the valid ones', () => {
      const result = validateTabReadyEvent({
        ...base,
        crossTabLinks: [{ targetTab: 'quiz', label: 'Quiz' }, { targetTab: 3 }],
        attachments: [{ slot: 'top', component: 'tip_callout', props: { text: 'Tip' } }, { slot: 'side' }],
      });

      expect([result.crossTabLinks?.length, result.attachments?.length]).toEqual([1, 1]);
    });

    it('should return an empty tab for a non-object event', () => {
      const result = validateTabReadyEvent('not an event');

      expect(result).toEqual({ id: '', label: '', emoji: '', component: '', props: {} });
    });
  });
});
