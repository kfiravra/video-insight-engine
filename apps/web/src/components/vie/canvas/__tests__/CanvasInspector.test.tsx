import { describe, it, expect, vi } from 'vitest';
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import type { ConceptItem } from '@vie/types';

import { CanvasInspector, type InspectorNeighbor } from '../CanvasInspector';

const concept: ConceptItem = {
  name: 'Gravity',
  emoji: '🌍',
  definition: 'Force that attracts objects with mass toward each other.',
  example: 'An apple falling from a tree.',
  analogy: 'Like a magnet, but for everything with mass.',
  group: 'Foundations',
  connections: [],
};

const neighbors: InspectorNeighbor[] = [
  { id: 'concept-1', name: 'Acceleration', emoji: '🚀', relation: 'causes' },
];

function renderInspector(overrides: Partial<React.ComponentProps<typeof CanvasInspector>> = {}) {
  const props = {
    concept,
    neighbors,
    onSelectNeighbor: vi.fn(),
    onClose: vi.fn(),
    isDesktop: true,
    ...overrides,
  };
  render(<CanvasInspector {...props} />);
  return props;
}

describe('CanvasInspector', () => {
  it('renders the concept definition, example and analogy', () => {
    renderInspector();
    expect(
      screen.getByText('Force that attracts objects with mass toward each other.'),
    ).toBeInTheDocument();
    expect(screen.getByText('An apple falling from a tree.')).toBeInTheDocument();
    expect(screen.getByText('Like a magnet, but for everything with mass.')).toBeInTheDocument();
  });

  it('calls onSelectNeighbor with the neighbour id when a chip is clicked', async () => {
    const user = userEvent.setup();
    const { onSelectNeighbor } = renderInspector();
    await user.click(screen.getByRole('button', { name: /Acceleration/ }));
    expect(onSelectNeighbor).toHaveBeenCalledWith('concept-1');
  });

  it('calls onClose when the close button is clicked', async () => {
    const user = userEvent.setup();
    const { onClose } = renderInspector();
    await user.click(screen.getByRole('button', { name: /close details/i }));
    expect(onClose).toHaveBeenCalled();
  });

  it('renders frame evidence when a frame is present', () => {
    renderInspector({
      concept: { ...concept, thumbnailUrl: 'https://cdn/frame.jpg', frameCaption: 'A diagram' },
    });
    expect(screen.getByRole('img')).toBeInTheDocument();
  });

  it('should carry the full concept name as the heading title when it is clamped to two lines', () => {
    const name = 'Reinforcement learning from human feedback with a learned reward model';
    renderInspector({ concept: { ...concept, name } });
    expect(screen.getByRole('heading', { name })).toHaveAttribute('title', name);
  });

  it('omits frame evidence gracefully when none is present', () => {
    renderInspector();
    expect(screen.queryByRole('img')).toBeNull();
  });
});
