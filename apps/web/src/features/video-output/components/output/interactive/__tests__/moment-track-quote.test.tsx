import { describe, it, expect } from 'vitest';
import { render, screen } from '@testing-library/react';

import { MomentTrack, type MomentItem } from '../MomentTrack';

// p1 review R4/R7: a moment row shows the label, what the moment established and the
// speaker's line (the learning schema's UI line); an interview exchange heads with its
// whole question when the label is only its first sentence.
const described: MomentItem[] = [
  {
    time: '20:45',
    seconds: 1245,
    label: 'Hand-written code is done',
    description: 'Agents now write more code in a month than the team wrote in a year.',
    quote: 'Pencils down.',
  },
  {
    time: '38:15',
    seconds: 2295,
    label: 'English beats Ruby',
    description: 'Its vagueness is what makes it expressive.',
  },
];

const longQuestion =
  'Who reviews the code? And when an agent writes 150,000 lines a month, does anyone still read them before they ship to customers?';
const interview: MomentItem[] = [
  { time: '4:10', seconds: 250, label: 'Who reviews the code?', question: longQuestion, description: 'Another agent does, and a human signs off.' },
  { time: '9:30', seconds: 570, label: 'Is Ruby dead?', description: 'No — it reads like English.' },
];

describe('MomentTrack', () => {
  describe('timeline row text', () => {
    it("should show the speaker's line when the moment carries a quote", () => {
      render(<MomentTrack items={described} />);

      expect(screen.getByText('Pencils down.')).toBeInTheDocument();
    });

    it('should head the row with the whole question when it differs from the label', () => {
      render(<MomentTrack items={interview} />);

      expect(screen.getByText(longQuestion)).toBeInTheDocument();
    });

    it('should not repeat the question head as a second line when the whole question shows', () => {
      render(<MomentTrack items={interview} />);

      expect(screen.queryByText('Who reviews the code?')).not.toBeInTheDocument();
    });
  });
});
