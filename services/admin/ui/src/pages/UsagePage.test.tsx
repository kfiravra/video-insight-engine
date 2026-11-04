import { describe, it, expect, vi } from 'vitest';
import { render, screen } from '@testing-library/react';

// Every panel is a stub that names itself, so the test reads the page's composition.
function stub(name: string) {
  return { [name]: ({ days }: { days?: number }) => <div data-testid="usage-panel">{`${name}:${days ?? '-'}`}</div> };
}

vi.mock('../components/CostChart', () => stub('CostChart'));
vi.mock('../components/ModelBreakdown', () => stub('ModelBreakdown'));
vi.mock('../components/FeatureBreakdown', () => stub('FeatureBreakdown'));
vi.mock('../components/LatencyPanel', () => stub('LatencyPanel'));
vi.mock('../components/PipelineRunsPanel', () => stub('PipelineRunsPanel'));
vi.mock('../components/SessionsPanel', () => stub('SessionsPanel'));
vi.mock('../components/AnomaliesPanel', () => stub('AnomaliesPanel'));
vi.mock('../components/DuplicatesPanel', () => stub('DuplicatesPanel'));
vi.mock('../components/RecentCalls', () => stub('RecentCalls'));

import { UsagePage } from './UsagePage';

describe('UsagePage', () => {
  it('should place the chat sessions panel right after the pipeline runs panel', () => {
    render(<UsagePage days={14} />);
    const panels = screen.getAllByTestId('usage-panel').map((el) => el.textContent);
    const runs = panels.indexOf('PipelineRunsPanel:14');
    expect(runs).toBeGreaterThanOrEqual(0);
    expect(panels[runs + 1]).toBe('SessionsPanel:14');
  });

  it('should hand every panel the selected window', () => {
    render(<UsagePage days={14} />);
    const panels = screen.getAllByTestId('usage-panel').map((el) => el.textContent);
    expect(panels).toEqual([
      'CostChart:14',
      'ModelBreakdown:14',
      'FeatureBreakdown:14',
      'LatencyPanel:14',
      'PipelineRunsPanel:14',
      'SessionsPanel:14',
      'AnomaliesPanel:14',
      'DuplicatesPanel:14',
      'RecentCalls:-',
    ]);
  });
});
