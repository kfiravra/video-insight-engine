import { describe, it, expect, vi } from 'vitest';
import { render, screen } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';

vi.mock('../hooks/use-admin-api', () => ({
  useUsageDuplicates: vi.fn(),
}));

import { useUsageDuplicates } from '../hooks/use-admin-api';
import { DuplicatesPanel } from './DuplicatesPanel';

const wrapper = ({ children }: { children: React.ReactNode }) => (
  <QueryClientProvider client={new QueryClient()}>{children}</QueryClientProvider>
);

describe('DuplicatesPanel', () => {
  it('should list repeated prompts with count and cost', () => {
    vi.mocked(useUsageDuplicates).mockReturnValue({
      data: [{ prompt_hash: 'abc', count: 4, total_cost_usd: 0.4, model: 'm', feature: 'summarize:classify', prompt_preview: 'Classify this video' }],
      isLoading: false,
      isError: false,
    } as unknown as ReturnType<typeof useUsageDuplicates>);
    render(<DuplicatesPanel />, { wrapper });
    expect(screen.getByText('Classify this video')).toBeTruthy();
    expect(screen.getByText('4')).toBeTruthy();
    expect(screen.getByText('$0.4000')).toBeTruthy();
  });

  it.each([
    ['an empty string (assistant chat rows)', ''],
    ['null (a row older than 90 days)', null],
    ['absent', undefined],
  ])('should show the prompt hash when the preview is %s', (_label, preview) => {
    vi.mocked(useUsageDuplicates).mockReturnValue({
      data: [{ prompt_hash: 'abc123hash', count: 4, total_cost_usd: 0.4, model: 'm', feature: 'rag_chat', prompt_preview: preview }],
      isLoading: false,
      isError: false,
    } as unknown as ReturnType<typeof useUsageDuplicates>);
    render(<DuplicatesPanel />, { wrapper });
    expect(screen.getByText('abc123hash')).toBeTruthy();
  });

  it('should render an empty state', () => {
    vi.mocked(useUsageDuplicates).mockReturnValue({ data: [], isLoading: false, isError: false } as unknown as ReturnType<typeof useUsageDuplicates>);
    render(<DuplicatesPanel />, { wrapper });
    expect(screen.getByText('No repeated prompts')).toBeTruthy();
  });
});
