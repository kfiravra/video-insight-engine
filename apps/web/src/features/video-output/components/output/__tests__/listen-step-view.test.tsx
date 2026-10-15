import { useState } from 'react';
import { describe, it, expect, vi } from 'vitest';
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { TabStateProvider } from '@/features/video-output/contexts/TabStateContext';
import { ListenStepView, type ListenSegment } from '../RecipeStepView';

const SEGMENTS: ListenSegment[] = [
  {
    label: 'Is there intelligent life out there?',
    description: 'Tegmark guesses no: the Great Filter is probably behind us.',
    seconds: 148,
    endSeconds: 344,
  },
  { label: 'The Great Filter', seconds: 344 },
];

function ListenHarness({ segments = SEGMENTS, onSeek }: { segments?: ListenSegment[]; onSeek?: (s: number) => void }) {
  const [currentStep, setCurrentStep] = useState(0);
  return (
    <TabStateProvider videoId="listen-step-test">
      <ListenStepView
        segments={segments}
        currentStep={currentStep}
        onStepChange={setCurrentStep}
        onComplete={vi.fn()}
        onSeek={onSeek}
      />
    </TabStateProvider>
  );
}

describe('ListenStepView', () => {
  it('should show the segment description when the source has one', () => {
    render(<ListenHarness />);
    expect(screen.getByText(/Great Filter is probably behind us/)).toBeInTheDocument();
  });

  it('should show the play range as a seek button when a seek handler exists', () => {
    render(<ListenHarness onSeek={vi.fn()} />);
    expect(screen.getByRole('button', { name: 'Play 2:28 – 5:44' })).toBeInTheDocument();
  });

  it('should seek to the segment start when the play range is clicked', async () => {
    const user = userEvent.setup();
    const onSeek = vi.fn();
    render(<ListenHarness onSeek={onSeek} />);
    await user.click(screen.getByRole('button', { name: 'Play 2:28 – 5:44' }));
    expect(onSeek).toHaveBeenCalledWith(148);
  });

  it('should show the segment length next to the range', () => {
    render(<ListenHarness />);
    expect(screen.getByText('3m 16s')).toBeInTheDocument();
  });

  it('should count segments, not steps, in the header', () => {
    render(<ListenHarness />);
    expect(screen.getByText('Segment 1 of 2')).toBeInTheDocument();
  });

  it('should render hour-long positions with an hour field', () => {
    render(<ListenHarness segments={[{ label: 'Late', seconds: 3700, endSeconds: 3900 }]} />);
    expect(screen.getByText('Play 1:01:40 – 1:05:00')).toBeInTheDocument();
  });

  it('should show no play range when the segment has no start', () => {
    render(<ListenHarness segments={[{ label: 'Unstamped' }]} />);
    expect(screen.queryByText(/^Play/)).not.toBeInTheDocument();
  });

  it('should show an open-ended range for the last segment without repeating the label as copy', async () => {
    const user = userEvent.setup();
    render(<ListenHarness />);
    await user.click(screen.getByRole('button', { name: /Next/ }));
    expect(screen.getByText('Play from 5:44')).toBeInTheDocument();
    expect(screen.getAllByText('The Great Filter')).toHaveLength(1);
  });
});
