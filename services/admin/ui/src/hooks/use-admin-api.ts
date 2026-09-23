import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { api } from '../lib/api';

export function useUsageStats(days = 30) {
  return useQuery({ queryKey: ['usage-stats', days], queryFn: () => api.usage.stats(days), refetchInterval: 30_000 });
}

export function useUsageDaily(days = 30) {
  return useQuery({ queryKey: ['usage-daily', days], queryFn: () => api.usage.daily(days), refetchInterval: 30_000 });
}

interface EnabledOption {
  enabled?: boolean;
}

export function useUsageByFeature(days = 30, options?: EnabledOption) {
  return useQuery({
    queryKey: ['usage-by-feature', days],
    queryFn: () => api.usage.byFeature(days),
    enabled: options?.enabled ?? true,
  });
}

export function useUsageByModel(days = 30, options?: EnabledOption) {
  return useQuery({
    queryKey: ['usage-by-model', days],
    queryFn: () => api.usage.byModel(days),
    enabled: options?.enabled ?? true,
  });
}

export function useUsageByService(days = 30) {
  return useQuery({ queryKey: ['usage-by-service', days], queryFn: () => api.usage.byService(days) });
}

export function useUsageByVideo(days = 30, limit = 50) {
  return useQuery({ queryKey: ['usage-by-video', days, limit], queryFn: () => api.usage.byVideo(days, limit) });
}

export function useVideoDetail(videoId: string | undefined) {
  return useQuery({
    queryKey: ['video-detail', videoId],
    queryFn: () => api.usage.forVideo(videoId!),
    enabled: !!videoId,
    staleTime: 60_000,
  });
}

export function useUsageRecent(limit = 20) {
  return useQuery({ queryKey: ['usage-recent', limit], queryFn: () => api.usage.recent(limit), refetchInterval: 10_000 });
}

export function useUsageDuplicates(days = 7) {
  return useQuery({ queryKey: ['usage-duplicates', days], queryFn: () => api.usage.duplicates(days) });
}

export function useUsageAnomalies(thresholdUsd = 0.5, days = 7) {
  return useQuery({
    queryKey: ['usage-anomalies', thresholdUsd, days],
    queryFn: () => api.usage.anomalies(thresholdUsd, days),
  });
}

export function useHealthServices() {
  return useQuery({ queryKey: ['health-services'], queryFn: () => api.health.services(), refetchInterval: 15_000 });
}

export function useHealthOverview() {
  return useQuery({ queryKey: ['health-overview'], queryFn: () => api.health.overview(), refetchInterval: 15_000 });
}

export function useHealthUptime(days = 7) {
  return useQuery({ queryKey: ['health-uptime', days], queryFn: () => api.health.uptime(days) });
}

export function useQueueStats() {
  return useQuery({
    queryKey: ['queue-stats'],
    queryFn: () => api.queue.stats(),
    refetchInterval: 10_000,
  });
}

export function useQueueDlq(limit = 20) {
  return useQuery({
    queryKey: ['queue-dlq', limit],
    queryFn: () => api.queue.dlq(limit),
    refetchInterval: 15_000,
  });
}

/**
 * Replay DLQ messages; refreshes the queue counters and the DLQ peek once the
 * call settles. Settled, not success: vie-api drains with per-message confirms,
 * so a timeout or 502 can land after part of the batch was already re-published.
 */
export function useReplayDlq() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (max: number) => api.queue.replay(max),
    onSettled: () => {
      void queryClient.invalidateQueries({ queryKey: ['queue-stats'] });
      void queryClient.invalidateQueries({ queryKey: ['queue-dlq'] });
    },
  });
}

/**
 * Global video delete (every store, every user). The detail query is
 * invalidated so the page reflects the purge; llm_usage rows are kept by
 * design, so the cost breakdown stays with `video: null`.
 */
export function usePurgeVideo(videoId: string) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (reason?: string) => api.videos.purge(videoId, reason),
    onSettled: () => {
      void queryClient.invalidateQueries({ queryKey: ['video-detail', videoId] });
      void queryClient.invalidateQueries({ queryKey: ['usage-by-video'] });
    },
  });
}

export function useAlertsRecent(limit = 20) {
  return useQuery({ queryKey: ['alerts-recent', limit], queryFn: () => api.alerts.recent(limit), refetchInterval: 30_000 });
}

export function useAlertConfig() {
  return useQuery({ queryKey: ['alert-config'], queryFn: () => api.alerts.config() });
}

export function useUsageByOutputType(days = 30, options?: EnabledOption) {
  return useQuery({
    queryKey: ['usage-by-output-type', days],
    queryFn: () => api.usage.byOutputType(days),
    enabled: options?.enabled ?? true,
  });
}

export function useSharesTop(days = 30, limit = 10) {
  return useQuery({ queryKey: ['shares-top', days, limit], queryFn: () => api.shares.top(days, limit) });
}

export function useSharesStats(days = 30) {
  return useQuery({ queryKey: ['shares-stats', days], queryFn: () => api.shares.stats(days) });
}

export function useTierDistribution() {
  return useQuery({ queryKey: ['tier-distribution'], queryFn: () => api.tiers.distribution() });
}

export function useUserCosts(days = 7, limit = 50, offset = 0) {
  return useQuery({
    queryKey: ['user-costs', days, limit, offset],
    queryFn: () => api.users.costs(days, limit, offset),
    refetchInterval: 60_000,
  });
}

export function useUserCostDetail(userId: string | null | undefined, days = 30) {
  return useQuery({
    queryKey: ['user-cost-detail', userId, days],
    queryFn: () => api.users.costDetail(userId!, days),
    enabled: !!userId,
  });
}

export function useUsageByRun(days = 30, limit = 20, offset = 0) {
  return useQuery({
    queryKey: ['usage-by-run', days, limit, offset],
    queryFn: () => api.usage.byRun(days, limit, offset),
    staleTime: 30_000,
  });
}

export function useUserActivity(userId: string | null | undefined) {
  return useQuery({
    queryKey: ['user-activity', userId],
    queryFn: () => api.users.activity(userId!),
    enabled: !!userId,
    staleTime: 30_000,
  });
}
