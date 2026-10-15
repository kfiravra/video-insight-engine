import { describe, it, expect, vi } from 'vitest';
import { render, screen, fireEvent, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import type { ConceptItem } from '@vie/types';

import { ConceptCanvas } from '../ConceptCanvas';
import { buildConceptGraph } from '../concept-graph';

const concepts: ConceptItem[] = [
  {
    name: 'Gravity',
    emoji: '🌍',
    definition: 'Force that attracts objects with mass toward each other.',
    example: 'An apple falling from a tree.',
    timestamp: 90,
    group: 'Foundations',
    connections: [{ to: 'Acceleration', type: 'causes' }],
  },
  {
    name: 'Acceleration',
    emoji: '🚀',
    definition: 'The rate at which velocity changes over time.',
    group: 'Mechanics',
    connections: [{ to: 'Gravity', type: 'relatesTo' }, { to: 'Phantom Concept', type: 'causes' }],
  },
  {
    name: 'Mass',
    emoji: '⚖️',
    definition: 'The amount of matter in an object.',
    group: 'Foundations',
    connections: [],
  },
];

// Legacy cached shape — bare-string connections, no group.
const legacyConcepts: ConceptItem[] = [
  { name: 'Embedding', emoji: '🔢', definition: 'Maps tokens to vectors.', connections: ['Attention'] },
  { name: 'Attention', emoji: '👀', definition: 'Weights tokens.', connections: ['Embedding'] },
];

describe('buildConceptGraph', () => {
  it('builds typed edges and drops hallucinated + self references', () => {
    const model = buildConceptGraph(concepts, ['Foundations', 'Mechanics']);
    // Gravity↔Acceleration is one undirected edge; the "Phantom Concept" ref is dropped.
    expect(model.edges).toHaveLength(1);
    expect(model.edges[0]).toMatchObject({ relation: 'causes' });
  });

  it('coerces legacy bare-string connections to relatesTo edges', () => {
    const model = buildConceptGraph(legacyConcepts);
    expect(model.edges).toHaveLength(1);
    expect(model.edges[0].relation).toBe('relatesTo');
  });

  it('returns ordered, de-duped group lanes', () => {
    const model = buildConceptGraph(concepts, ['Foundations', 'Mechanics']);
    expect(model.groups).toEqual(['Foundations', 'Mechanics']);
  });

  it('derives a default group lane for ungrouped legacy concepts', () => {
    const model = buildConceptGraph(legacyConcepts);
    expect(model.groups).toEqual(['Concepts']);
  });

  it('records undirected adjacency for the inspector', () => {
    const model = buildConceptGraph(concepts, ['Foundations', 'Mechanics']);
    const gravityNeighbors = model.adjacency.get('concept-0') ?? [];
    expect(gravityNeighbors.map((n) => n.name)).toContain('Acceleration');
  });
});

describe('ConceptCanvas', () => {
  it('renders the empty fallback when there are no concepts', () => {
    render(<ConceptCanvas concepts={[]} />);
    expect(screen.getByText(/No concepts were extracted/i)).toBeInTheDocument();
  });

  it('renders a sparse graph (concepts with no connections → no edges, no crash)', () => {
    const sparse: ConceptItem[] = [
      { name: 'Alpha', emoji: '🅰️', definition: 'First.', connections: [] },
      { name: 'Beta', emoji: '🅱️', definition: 'Second.', connections: [] },
    ];
    render(<ConceptCanvas concepts={sparse} />);
    // Falls back to a single derived "Concepts" lane with zero links.
    expect(screen.getByTestId('concept-canvas-counts')).toHaveTextContent(
      '2 concepts · 1 groups · 0 links',
    );
    expect(screen.getByRole('heading', { name: 'Concepts' })).toBeInTheDocument();
  });

  it('shows a header strip with concept, group and link counts', () => {
    render(<ConceptCanvas concepts={concepts} groups={['Foundations', 'Mechanics']} />);
    expect(screen.getByTestId('concept-canvas-counts')).toHaveTextContent(
      '3 concepts · 2 groups · 1 links',
    );
  });

  it('opens in the list view by default', () => {
    render(<ConceptCanvas concepts={concepts} groups={['Foundations', 'Mechanics']} />);
    // Group headings are present; the canvas node layer is not.
    expect(screen.getByRole('heading', { name: 'Foundations' })).toBeInTheDocument();
    expect(document.querySelector('[data-slot="vie-concept-node"]')).toBeNull();
  });

  it('opens in the list view on desktop too — the map is a toggle (D8 / R8)', () => {
    const original = window.matchMedia;
    window.matchMedia = vi.fn().mockImplementation((query: string) => ({
      ...original(query),
      matches: /min-width: 1024px/.test(query),
    }));
    try {
      render(<ConceptCanvas concepts={concepts} groups={['Foundations', 'Mechanics']} />);
      expect(screen.getByRole('button', { name: /^list$/i })).toHaveAttribute('aria-pressed', 'true');
      expect(document.querySelector('.react-flow')).toBeNull();
    } finally {
      window.matchMedia = original;
    }
  });

  it('shows definition, example and timestamp inline per concept without a click (R8)', () => {
    render(<ConceptCanvas concepts={concepts} groups={['Foundations', 'Mechanics']} />);
    expect(
      screen.getByText('Force that attracts objects with mass toward each other.'),
    ).toBeInTheDocument();
    expect(screen.getByText('An apple falling from a tree.')).toBeInTheDocument();
    expect(screen.getByText('1:30')).toBeInTheDocument();
  });

  it('seeks the video from the inline timestamp chip', async () => {
    const user = userEvent.setup();
    const onSeek = vi.fn();
    render(<ConceptCanvas concepts={concepts} groups={['Foundations', 'Mechanics']} onSeek={onSeek} />);
    await user.click(screen.getByRole('button', { name: /jump to 1:30/i }));
    expect(onSeek).toHaveBeenCalledWith(90);
  });

  it('keeps the relation legend to the map view', async () => {
    const user = userEvent.setup();
    render(<ConceptCanvas concepts={concepts} groups={['Foundations', 'Mechanics']} />);
    expect(screen.queryByText('contrasts')).toBeNull();
    await user.click(screen.getByRole('button', { name: /^map$/i }));
    expect(screen.getByText('contrasts')).toBeInTheDocument();
  });

  it('returns from the map to the list view', async () => {
    const user = userEvent.setup();
    render(<ConceptCanvas concepts={concepts} groups={['Foundations', 'Mechanics']} />);
    await user.click(screen.getByRole('button', { name: /^map$/i }));
    await user.click(screen.getByRole('button', { name: /^list$/i }));
    expect(document.querySelector('.react-flow')).toBeNull();
    expect(screen.getByRole('heading', { name: 'Mechanics' })).toBeInTheDocument();
  });

  it('switches to the Map view and renders one node per concept', async () => {
    const user = userEvent.setup();
    render(<ConceptCanvas concepts={concepts} groups={['Foundations', 'Mechanics']} />);
    await user.click(screen.getByRole('button', { name: /^map$/i }));
    expect(document.querySelectorAll('[data-slot="vie-concept-node"]')).toHaveLength(concepts.length);
  });

  it('gives each node hidden source + target handles (regression: floating edges need anchors)', async () => {
    // Without handles on the (handle-less) compact nodes, React Flow logs
    // error #008 ("Couldn't create edge for source handle id: null") and draws
    // ZERO edges — the typed-edge feature silently breaks. Each node must carry
    // one source + one target handle (hidden) for the floating edges to anchor.
    // (jsdom can't render RF's measured edge layer, so we assert the anchors.)
    const user = userEvent.setup();
    render(<ConceptCanvas concepts={concepts} groups={['Foundations', 'Mechanics']} />);
    await user.click(screen.getByRole('button', { name: /^map$/i }));
    const nodes = document.querySelectorAll('[data-slot="vie-concept-node"]');
    nodes.forEach((node) => {
      expect(node.querySelectorAll('.react-flow__handle.source').length).toBe(1);
      expect(node.querySelectorAll('.react-flow__handle.target').length).toBe(1);
    });
  });

  it('opens the inspector (outside the node tree) when a concept is selected', async () => {
    const user = userEvent.setup();
    render(<ConceptCanvas concepts={concepts} groups={['Foundations', 'Mechanics']} />);
    // No inspector before selection.
    expect(document.querySelector('[data-slot="canvas-inspector"]')).toBeNull();
    // Select Gravity from the (default) list view.
    await user.click(screen.getByRole('button', { name: /Gravity/ }));
    const inspector = document.querySelector('[data-slot="canvas-inspector"]');
    expect(inspector).not.toBeNull();
    expect(
      within(inspector as HTMLElement).getByText(
        'Force that attracts objects with mass toward each other.',
      ),
    ).toBeInTheDocument();
  });

  it('renders the inspector inline under the selected concept in the list view', async () => {
    const user = userEvent.setup();
    render(<ConceptCanvas concepts={concepts} groups={['Foundations', 'Mechanics']} />);
    const nameButton = screen.getByRole('button', { name: /Gravity/ });
    await user.click(nameButton);
    const inspector = document.querySelector('[data-slot="canvas-inspector"]') as HTMLElement;
    expect(inspector.getAttribute('data-placement')).toBe('inline');
    expect(inspector.className).not.toMatch(/absolute/);
    // Same list item as the concept it describes.
    expect(nameButton.closest('li')).toBe(inspector.closest('li'));
  });

  it('floats the inspector over the canvas in the Map view', async () => {
    const user = userEvent.setup();
    render(<ConceptCanvas concepts={concepts} groups={['Foundations', 'Mechanics']} />);
    await user.click(screen.getByRole('button', { name: /^map$/i }));
    // xyflow keeps nodes hidden until measured (no jsdom layout) — click the raw node button.
    fireEvent.click(document.querySelector('[data-slot="vie-concept-node"] button') as HTMLElement);
    const inspector = document.querySelector('[data-slot="canvas-inspector"]') as HTMLElement;
    expect(inspector.getAttribute('data-placement')).toBe('overlay');
    expect(inspector.className).toMatch(/absolute/);
  });

  it('locks node dragging in the Map view (no infinite drag)', async () => {
    const user = userEvent.setup();
    render(<ConceptCanvas concepts={concepts} groups={['Foundations', 'Mechanics']} />);
    await user.click(screen.getByRole('button', { name: /^map$/i }));
    // ReactFlow tags draggable nodes with a `draggable` class; locked nodes have none.
    expect(document.querySelectorAll('.react-flow__node.draggable')).toHaveLength(0);
  });

  it('does not persist any layout to localStorage (feature dropped)', () => {
    const setItemSpy = vi.spyOn(window.localStorage, 'setItem');
    render(<ConceptCanvas concepts={concepts} groups={['Foundations', 'Mechanics']} />);
    fireEvent.click(screen.getByRole('button', { name: /Gravity/ }));
    expect(setItemSpy).not.toHaveBeenCalledWith(
      expect.stringContaining('concept-canvas'),
      expect.anything(),
    );
    setItemSpy.mockRestore();
  });
});
