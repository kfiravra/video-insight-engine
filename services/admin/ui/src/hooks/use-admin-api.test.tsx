import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { renderHook, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import type { ReactNode } from 'react';

// Hooks are thin wrappers around useQuery — test that they export correctly
describe('use-admin-api hooks', () => {
  it('should export all hook functions', async () => {
    const hooks = await import('./use-admin-api');
    expect(typeof hooks.useUsageStats).toBe('function');
    expect(typeof hooks.useUsageDaily).toBe('function');
    expect(typeof hooks.useUsageByFeature).toBe('function');
    expect(typeof hooks.useUsageByModel).toBe('function');
    expect(typeof hooks.useUsageByService).toBe('function');
    expect(typeof hooks.useUsageByVideo).toBe('function');
    expect(typeof hooks.useUsageRecent).toBe('function');
    expect(typeof hooks.useUsageDuplicates).toBe('function');
    expect(typeof hooks.useHealthServices).toBe('function');
    expect(typeof hooks.useHealthOverview).toBe('function');
    expect(typeof hooks.useHealthUptime).toBe('function');
    expect(typeof hooks.useAlertsRecent).toBe('function');
    expect(typeof hooks.useAlertConfig).toBe('function');
    // New hooks
    expect(typeof hooks.useUsageByRun).toBe('function');
    expect(typeof hooks.useUsageBySession).toBe('function');
    expect(typeof hooks.useUserActivity).toBe('function');
    // observability-fix 2.1 — previously unwired endpoints
    expect(typeof hooks.useUsageAnomalies).toBe('function');
    expect(typeof hooks.useQueueDlq).toBe('function');
    expect(typeof hooks.useReplayDlq).toBe('function');
  });
});

describe('use-admin-api enabled option', () => {
  const byModelMock = vi.fn();
  const byFeatureMock = vi.fn();
  const byOutputTypeMock = vi.fn();

  beforeEach(() => {
    byModelMock.mockReset();
    byFeatureMock.mockReset();
    byOutputTypeMock.mockReset();
    byModelMock.mockResolvedValue([]);
    byFeatureMock.mockResolvedValue([]);
    byOutputTypeMock.mockResolvedValue([]);

    vi.doMock('../lib/api', () => ({
      api: {
        usage: {
          byModel: byModelMock,
          byFeature: byFeatureMock,
          byOutputType: byOutputTypeMock,
        },
      },
    }));
  });

  afterEach(() => {
    vi.doUnmock('../lib/api');
    vi.resetModules();
  });

  const makeWrapper = () => {
    const client = new QueryClient({
      defaultOptions: { queries: { retry: false, gcTime: 0 } },
    });
    return ({ children }: { children: ReactNode }) => (
      <QueryClientProvider client={client}>{children}</QueryClientProvider>
    );
  };

  it('should NOT fetch useUsageByModel when enabled:false', async () => {
    const { useUsageByModel } = await import('./use-admin-api');
    const wrapper = makeWrapper();
    const { result } = renderHook(() => useUsageByModel(7, { enabled: false }), { wrapper });
    // Give React Query a tick; it should NOT call the fetcher
    await new Promise((r) => setTimeout(r, 30));
    expect(byModelMock).not.toHaveBeenCalled();
    expect(result.current.fetchStatus).toBe('idle');
  });

  it('should fetch useUsageByModel when enabled:true (default)', async () => {
    const { useUsageByModel } = await import('./use-admin-api');
    const wrapper = makeWrapper();
    renderHook(() => useUsageByModel(7), { wrapper });
    await waitFor(() => expect(byModelMock).toHaveBeenCalledWith(7));
  });

  it('should NOT fetch useUsageByFeature when enabled:false', async () => {
    const { useUsageByFeature } = await import('./use-admin-api');
    const wrapper = makeWrapper();
    renderHook(() => useUsageByFeature(7, { enabled: false }), { wrapper });
    await new Promise((r) => setTimeout(r, 30));
    expect(byFeatureMock).not.toHaveBeenCalled();
  });

  it('should NOT fetch useUsageByOutputType when enabled:false', async () => {
    const { useUsageByOutputType } = await import('./use-admin-api');
    const wrapper = makeWrapper();
    renderHook(() => useUsageByOutputType(7, { enabled: false }), { wrapper });
    await new Promise((r) => setTimeout(r, 30));
    expect(byOutputTypeMock).not.toHaveBeenCalled();
  });
});
