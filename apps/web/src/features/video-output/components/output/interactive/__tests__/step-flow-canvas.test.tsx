import { describe, it, expect } from 'vitest';
import { render, fireEvent } from '@testing-library/react';
import type { ReactElement } from 'react';
import type { StepItem } from '@vie/types';

import { StepFlowCanvas } from '../StepFlowCanvas';
import { zigzagLayout } from '../step-flow-layout';
import { TabStateProvider } from '@/features/video-output/contexts/TabStateContext';

const steps: StepItem[] = [
  { number: 1, title: 'Knead', instruction: 'Knead the dough for 10 minutes.', duration: '10 min' },
  { number: 2, title: 'Rest', instruction: 'Let the dough rest covered.' },
  { number: 3, title: 'Shape', instruction: 'Shape into rounds.', duration: '5 min' },
];

// Completion is sourced from TabStateContext so the canvas stays in sync with
// StepByStepInteractive — every render needs the provider.
function renderInProvider(ui: ReactElement, videoId = 'video-1') {
  return render(<TabStateProvider videoId={videoId}>{ui}</TabStateProvider>);
}

describe('StepFlowCanvas', () => {
  it('should render one node per step', () => {
    renderInProvider(<StepFlowCanvas steps={steps} />);
    const nodes = document.querySelectorAll('[data-slot="vie-step-node"]');
    expect(nodes).toHaveLength(steps.length);
  });

  it('should lock node dragging (nodesDraggable=false — no infinite drag)', () => {
    renderInProvider(<StepFlowCanvas steps={steps} />);
    // ReactFlow tags draggable nodes with a `draggable` class; locked nodes have none.
    expect(document.querySelectorAll('.react-flow__node.draggable')).toHaveLength(0);
  });

  it('should render edges between consecutive steps', () => {
    renderInProvider(<StepFlowCanvas steps={steps} />);
    // xyflow edges require a real viewport to render the path; we assert the
    // edges container mounted so the layer is wired up.
    expect(document.querySelector('.react-flow__edges')).not.toBeNull();
    // And the zigzag layout helper produces N positions.
    const positions = zigzagLayout(steps.length);
    expect(positions).toHaveLength(steps.length);
    expect(positions[0].x).toBeLessThan(0);
    expect(positions[1].x).toBeGreaterThan(0);
  });

  it('should toggle complete state when the checkbox is clicked', () => {
    renderInProvider(<StepFlowCanvas steps={steps} />);
    const checkbox = document.querySelector<HTMLButtonElement>(
      'button[aria-label="Mark step 1 complete"]',
    );
    expect(checkbox).not.toBeNull();
    expect(checkbox!.getAttribute('aria-checked')).toBe('false');
    fireEvent.click(checkbox!);
    expect(
      document
        .querySelector('button[aria-label="Mark step 1 incomplete"]')
        ?.getAttribute('aria-checked'),
    ).toBe('true');
  });

  it('should reflect completion already present in shared tab state (regression: no desync)', () => {
    // Two canvases over the same steps share one TabStateProvider. Completing a
    // step in the first must surface as completed in the second — the sync the
    // shared-state refactor restores. Previously each canvas held a private
    // empty Set and the two views disagreed.
    renderInProvider(
      <>
        <div data-testid="a">
          <StepFlowCanvas steps={steps} tabId="steps-a" />
        </div>
        <div data-testid="b">
          <StepFlowCanvas steps={steps} tabId="steps-b" />
        </div>
      </>,
    );
    const firstStepACheckbox = document
      .querySelector('[data-testid="a"]')!
      .querySelector<HTMLButtonElement>('button[aria-label="Mark step 1 complete"]');
    fireEvent.click(firstStepACheckbox!);

    // The second canvas now shows step 1 as complete too (label flips).
    const secondCanvasComplete = document
      .querySelector('[data-testid="b"]')!
      .querySelector('button[aria-label="Mark step 1 incomplete"]');
    expect(secondCanvasComplete).not.toBeNull();
  });

  it('should show a compact duration chip for timed steps, not a per-node Timer (regression: step title must stay primary)', () => {
    renderInProvider(<StepFlowCanvas steps={steps} />);
    // Duration is a quiet chip now — a full countdown Timer (Start/Reset, big
    // ring) in every node out-shouted the step title. Query raw DOM because
    // xyflow keeps viewport children hidden until measured (no jsdom layout).
    const startButtons = document.querySelectorAll('button[aria-label="Start"]');
    expect(startButtons).toHaveLength(0);
    // Steps 1 (10 min) and 3 (5 min) carry durations; step 2 does not.
    const text = document.body.textContent ?? '';
    expect(text).toContain('10 min');
    expect(text).toContain('5 min');
  });
});
