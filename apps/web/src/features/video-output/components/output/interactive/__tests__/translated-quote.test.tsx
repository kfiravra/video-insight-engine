import { describe, it, expect } from 'vitest';
import { render, screen } from '@testing-library/react';

import { SpotExplorer } from '../SpotExplorer';
import { MomentTrack, type MomentItem } from '../MomentTrack';
import { DisplaySection } from '../../DisplaySection';
import { TAB_PROP_SCHEMAS } from '@/features/video-output/lib/tab-prop-schemas';

// output-quality 2.0 d (D12): a quote that is the English rendering of non-English
// speech is not the speaker's words — it reads as prose, never in quote styling.
const VERBATIM = 'Pencils down.';
const TRANSLATED = 'The real problem is who let it decide alone.';

const spots = [
  { name: 'Hand-written code is done', emoji: '💡', description: 'why', quote: VERBATIM },
  { name: 'Agents need a human gate', emoji: '💡', description: 'why', quote: TRANSLATED, quoteTranslated: true },
];

const moments: MomentItem[] = [
  { time: '0:20', seconds: 20, label: 'Pencils down', description: 'Code by hand is the exception.', quote: VERBATIM },
  { time: '1:15', seconds: 75, label: 'Who decides', description: 'The model must stop for approval.', quote: TRANSLATED, quoteTranslated: true },
];

const narrativeQuotes = [
  { text: VERBATIM, speaker: 'DHH' },
  { text: TRANSLATED, speaker: 'Host', quoteTranslated: true },
];

function parses(component: string, props: Record<string, unknown>): boolean {
  return TAB_PROP_SCHEMAS[component].schema.safeParse(props).success;
}

function quoteElement(text: string): Element | null {
  return screen.getByText(text).closest('blockquote');
}

describe('translated quotes', () => {
  describe('SpotExplorer', () => {
    it('should set a verbatim quote apart as a quotation when it is the speaker\'s words', () => {
      render(<SpotExplorer spots={spots} />);

      expect(quoteElement(VERBATIM)).not.toBeNull();
    });

    it('should render a translated quote as prose when the quote is flagged translated', () => {
      render(<SpotExplorer spots={spots} />);

      expect(quoteElement(TRANSLATED)).toBeNull();
    });
  });

  describe('MomentTrack', () => {
    it('should set a verbatim quote apart as a quotation when it is the speaker\'s words', () => {
      render(<MomentTrack items={moments} />);

      expect(quoteElement(VERBATIM)).not.toBeNull();
    });

    it('should render a translated quote as prose when the quote is flagged translated', () => {
      render(<MomentTrack items={moments} />);

      expect(quoteElement(TRANSLATED)).toBeNull();
    });
  });

  describe('DisplaySection quotes', () => {
    it('should render a translated line as prose when the quote is flagged translated', () => {
      render(<DisplaySection data={narrativeQuotes} />);

      expect(quoteElement(TRANSLATED)).toBeNull();
    });

    it('should keep the speaker beside a translated line when the quote is flagged translated', () => {
      render(<DisplaySection data={narrativeQuotes} />);

      expect(screen.getByText('— Host')).toBeInTheDocument();
    });
  });

  describe('tab prop schemas', () => {
    it('should accept the translated flag on a spot when it is a boolean', () => {
      expect(parses('spot_explorer', { spots })).toBe(true);
    });

    it('should accept the translated flag on a moment when it is a boolean', () => {
      expect(parses('moment_track', { items: moments })).toBe(true);
    });

    it('should refuse a translated flag that is not a boolean', () => {
      const bad = [{ ...moments[0], quoteTranslated: 'translated' }];

      expect(parses('moment_track', { items: bad })).toBe(false);
    });
  });
});
