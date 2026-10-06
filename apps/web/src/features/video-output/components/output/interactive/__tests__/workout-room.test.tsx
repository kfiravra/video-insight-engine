import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { render, screen, fireEvent, act } from '@testing-library/react';

vi.mock('../../Celebration', () => ({
  Celebration: ({ title }: { title: string }) => (
    <div data-testid="celebration">{title}</div>
  ),
}));

import { WorkoutRoom } from '../WorkoutRoom';
import type { FitnessExercise } from '@vie/types';

const exercises: FitnessExercise[] = [
  {
    name: 'Push-ups',
    emoji: '💪',
    sets: 2,
    reps: '10',
    difficulty: 'beginner',
    formCues: ['Keep back straight', 'Engage core'],
    modifications: [
      { label: 'Knee push-ups', description: 'Easier variant on the knees.' },
    ],
  },
  {
    name: 'Squats',
    emoji: '🦵',
    sets: 2,
    reps: '15',
    difficulty: 'intermediate',
    formCues: [],
    modifications: [],
  },
];

describe('WorkoutRoom', () => {
  beforeEach(() => {
    vi.useFakeTimers();
  });

  afterEach(() => {
    vi.useRealTimers();
  });

  it('should render the first exercise', () => {
    render(<WorkoutRoom exercises={exercises} />);
    expect(screen.getByText('Push-ups')).toBeInTheDocument();
    expect(screen.getByText('Exercise 1 of 2')).toBeInTheDocument();
    // Second exercise not shown yet
    expect(screen.queryByText('Squats')).not.toBeInTheDocument();
  });

  it('renders an empty state when exercises is empty', () => {
    const { container } = render(<WorkoutRoom exercises={[]} />);
    expect(container.textContent).toContain('No exercises were extracted');
  });

  it('should increment the set counter when Complete set is clicked', () => {
    render(<WorkoutRoom exercises={exercises} />);
    const indicator = screen.getByRole('img', {
      name: /sets complete/,
    });
    expect(indicator).toHaveAccessibleName('0 of 2 sets complete');
    fireEvent.click(screen.getByRole('button', { name: /complete set/i }));
    expect(indicator).toHaveAccessibleName('1 of 2 sets complete');
  });

  it('should auto-advance to the next exercise after the rest interval', () => {
    render(<WorkoutRoom exercises={exercises} />);
    const button = screen.getByRole('button', { name: /complete set/i });
    // Two sets to finish exercise 1
    act(() => {
      fireEvent.click(button);
    });
    act(() => {
      fireEvent.click(button);
    });
    // Rest banner should now be present
    expect(screen.getByText(/Resting/i)).toBeInTheDocument();
    // Advance past the 3-second rest
    act(() => {
      vi.advanceTimersByTime(3500);
    });
    expect(screen.getByText('Squats')).toBeInTheDocument();
    expect(screen.getByText('Exercise 2 of 2')).toBeInTheDocument();
  });

  it("should restart the timer from the next exercise's duration after auto-advance", () => {
    const timed: FitnessExercise[] = [
      { name: 'Jumping jacks', emoji: '⚡', sets: 1, duration: '30 seconds', formCues: [], modifications: [] },
      { name: 'Wall sit', emoji: '🪑', sets: 1, duration: '45 seconds', formCues: [], modifications: [] },
    ];
    render(<WorkoutRoom exercises={timed} />);
    act(() => {
      fireEvent.click(screen.getByRole('button', { name: 'Start' }));
    });
    act(() => {
      vi.advanceTimersByTime(10000);
    });
    act(() => {
      fireEvent.click(screen.getByRole('button', { name: /complete set/i }));
    });
    act(() => {
      vi.advanceTimersByTime(3500);
    });
    expect(screen.getByText('Wall sit')).toBeInTheDocument();
    expect(screen.getByText('0:45')).toBeInTheDocument();
  });

  it('should NOT construct AudioContext when sound is disabled', () => {
    const audioSpy = vi.fn();
    const original = window.AudioContext;
    Object.defineProperty(window, 'AudioContext', {
      configurable: true,
      writable: true,
      value: audioSpy,
    });
    try {
      render(<WorkoutRoom exercises={exercises} />);
      const button = screen.getByRole('button', { name: /complete set/i });
      act(() => {
        fireEvent.click(button);
      });
      act(() => {
        fireEvent.click(button);
      });
      act(() => {
        vi.advanceTimersByTime(3500);
      });
      expect(audioSpy).not.toHaveBeenCalled();
    } finally {
      Object.defineProperty(window, 'AudioContext', {
        configurable: true,
        writable: true,
        value: original,
      });
    }
  });

  it('should play an audio cue when sound is enabled', () => {
    const start = vi.fn();
    const stop = vi.fn();
    const connect = vi.fn();
    const close = vi.fn().mockResolvedValue(undefined);
    const oscillator = {
      frequency: { value: 0 },
      type: 'sine',
      connect,
      start,
      stop,
    };
    const gain = { gain: { value: 0 }, connect };
    const audioCtx = {
      createOscillator: vi.fn(() => oscillator),
      createGain: vi.fn(() => gain),
      destination: {},
      currentTime: 0,
      close,
    };
    const audioSpy = vi.fn(() => audioCtx);
    const original = window.AudioContext;
    Object.defineProperty(window, 'AudioContext', {
      configurable: true,
      writable: true,
      value: audioSpy,
    });
    try {
      render(<WorkoutRoom exercises={exercises} />);
      // Enable sound first — while audio is disabled the toggle's accessible
      // name (aria-label) is "Enable audio cues" (the visible "Sound off" text
      // is overridden by the aria-label for the accessible name).
      fireEvent.click(
        screen.getByRole('button', { name: /enable audio cues/i }),
      );
      const button = screen.getByRole('button', { name: /complete set/i });
      act(() => {
        fireEvent.click(button);
      });
      act(() => {
        fireEvent.click(button);
      });
      // Trigger end-of-rest cue
      act(() => {
        vi.advanceTimersByTime(3500);
      });
      expect(audioSpy).toHaveBeenCalledTimes(1);
      expect(start).toHaveBeenCalled();
    } finally {
      Object.defineProperty(window, 'AudioContext', {
        configurable: true,
        writable: true,
        value: original,
      });
    }
  });

  it('should render form cues for the active exercise', () => {
    render(<WorkoutRoom exercises={exercises} />);
    expect(screen.getByText('Form cues')).toBeInTheDocument();
    expect(screen.getByText('Keep back straight')).toBeInTheDocument();
    expect(screen.getByText('Engage core')).toBeInTheDocument();
  });
});
