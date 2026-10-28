import { describe, it, expect, vi } from 'vitest';
import { render, screen } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { TierDistribution } from './TierDistribution';

vi.mock('../hooks/use-admin-api', () => ({
  useTierDistribution: vi.fn(),
}));

import { useTierDistribution } from '../hooks/use-admin-api';

const wrapper = ({ children }: { children: React.ReactNode }) => (
  <QueryClientProvider client={new QueryClient()}>{children}</QueryClientProvider>
);

describe('TierDistribution', () => {
  it('should render loading skeleton when loading', () => {
    vi.mocked(useTierDistribution).mockReturnValue({ data: undefined, isLoading: true } as unknown as ReturnType<typeof useTierDistribution>);
    const { container } = render(<TierDistribution />, { wrapper });
    expect(container.querySelector('.animate-pulse')).toBeTruthy();
  });

  it('should render empty state when data is empty', () => {
    vi.mocked(useTierDistribution).mockReturnValue({ data: [], isLoading: false } as unknown as ReturnType<typeof useTierDistribution>);
    render(<TierDistribution />, { wrapper });
    expect(screen.getByText('No plan data available')).toBeTruthy();
  });

  it('should render chart heading when data is present', () => {
    vi.mocked(useTierDistribution).mockReturnValue({
      data: [{ tier: 'free', count: 100, percentage: 80.0 }],
      isLoading: false,
    } as unknown as ReturnType<typeof useTierDistribution>);
    render(<TierDistribution />, { wrapper });
    expect(screen.getByText('User Plans')).toBeTruthy();
  });
});
