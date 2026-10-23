/// <reference types="@testing-library/jest-dom" />
import { describe, it, expect } from 'vitest';
import { render, screen } from '@testing-library/react';

import { MomentTrack, type MomentItem } from '../MomentTrack';

const QUESTION = 'Do you think machines will ever decide what we value, and who gets to check them?';

const exchange: MomentItem = {
  time: '4:10',
  seconds: 250,
  label: 'Do you think machines will ever decide what we value?',
  question: QUESTION,
  description: 'Not unless a human holds the gate.',
  speaker: 'Max Tegmark',
};

const plainMoment: MomentItem = {
  time: '1:00',
  seconds: 60,
  label: 'The demo starts',
  description: 'Shows the first build.',
  speaker: 'Host',
};

describe('MomentTimelineRow exchange', () => {
  it('should head the row with the whole question when the row is an exchange', () => {
    render(<MomentTrack items={[exchange]} />);
    expect(screen.getByText(QUESTION)).toBeInTheDocument();
  });

  it('should label the answer with its speaker when the row is an exchange', () => {
    render(<MomentTrack items={[exchange]} />);
    expect(screen.getByText('A · Max Tegmark')).toBeInTheDocument();
  });

  it('should show the answer text beside the speaker label when the row is an exchange', () => {
    render(<MomentTrack items={[exchange]} />);
    expect(screen.getByText('Not unless a human holds the gate.')).toBeInTheDocument();
  });

  it('should show the speaker once when the row is an exchange', () => {
    render(<MomentTrack items={[exchange]} />);
    expect(screen.queryAllByText(/Max Tegmark/)).toHaveLength(1);
  });

  it('should keep the header speaker chip when the row is a plain moment', () => {
    render(<MomentTrack items={[plainMoment]} />);
    expect(screen.getByText('Host')).toBeInTheDocument();
  });

  it('should not label an answer when the row is a plain moment', () => {
    render(<MomentTrack items={[plainMoment]} />);
    expect(screen.queryByText(/^A · /)).not.toBeInTheDocument();
  });

  describe('on a stored interview where only some rows kept their question', () => {
    // Rows stored before 4.1 lost `question` whenever it equalled the label.
    const storedRows: MomentItem[] = [
      exchange,
      { time: '6:00', seconds: 360, label: 'What should regulators do first?', description: 'License the largest training runs.', speaker: 'Max Tegmark' },
      { time: '8:20', seconds: 500, label: 'Is it too late?', description: 'No — but the window is closing.', speaker: 'Max Tegmark' },
    ];

    it('should label every answer with its speaker when one row of the track carries a question', () => {
      render(<MomentTrack items={storedRows} />);
      expect(screen.getAllByText('A · Max Tegmark')).toHaveLength(3);
    });

    it('should show no header speaker chip on any row when one row of the track carries a question', () => {
      render(<MomentTrack items={storedRows} />);
      expect(screen.queryByText('Max Tegmark')).not.toBeInTheDocument();
    });
  });

  describe('with a question the transcript does not say in those words', () => {
    const RENDERED_Q = 'האם מכונות יחליטו מה חשוב לנו?';
    const ENGLISH_Q = 'Will machines decide what matters to us?';
    const translatedExchange: MomentItem = {
      ...exchange,
      question: RENDERED_Q,
      questionEn: ENGLISH_Q,
      questionStatus: 'translated',
    };

    it('should not set a translated question as a quotation', () => {
      render(<MomentTrack items={[translatedExchange]} />);
      expect(screen.queryByText(RENDERED_Q)).not.toBeInTheDocument();
    });

    it('should head the row with the English question when the question is translated', () => {
      render(<MomentTrack items={[translatedExchange]} />);
      expect(screen.getByText(ENGLISH_Q).closest('blockquote')).toBeNull();
    });

    it('should head the row with the question as prose when a translated question has no English beside it', () => {
      render(<MomentTrack items={[{ ...exchange, questionStatus: 'translated' }]} />);
      expect(screen.getByText(QUESTION).closest('blockquote')).toBeNull();
    });
  });

  describe('with an English rendering of a Hebrew question', () => {
    const HEBREW_Q = 'האם מכונות יחליטו מה חשוב לנו?';
    const ENGLISH_Q = 'Will machines decide what matters to us?';
    const hebrewExchange: MomentItem = { ...exchange, question: HEBREW_Q, questionEn: ENGLISH_Q };

    it('should head the row with questionEn when it is present', () => {
      render(<MomentTrack items={[hebrewExchange]} />);
      expect(screen.getByText(ENGLISH_Q).tagName).toBe('P');
    });

    it('should show the verbatim question beneath as a quotation when questionEn heads the row', () => {
      render(<MomentTrack items={[hebrewExchange]} />);
      expect(screen.getByText(HEBREW_Q).closest('blockquote')).not.toBeNull();
    });

    it('should read the verbatim Hebrew question right to left when it is shown beneath', () => {
      render(<MomentTrack items={[hebrewExchange]} />);
      expect(screen.getByText(HEBREW_Q).closest('blockquote')).toHaveAttribute('dir', 'auto');
    });
  });
});
