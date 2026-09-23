const API_BASE = '';

export class ApiError extends Error {
  status: number;
  constructor(status: number, message: string) {
    super(message);
    this.name = 'ApiError';
    this.status = status;
  }
}

function getApiKey(): string | null {
  return localStorage.getItem('admin_api_key');
}

export function setApiKey(key: string) {
  localStorage.setItem('admin_api_key', key);
}

export function clearApiKey() {
  localStorage.removeItem('admin_api_key');
}

export function hasApiKey(): boolean {
  return !!getApiKey();
}

export function logout() {
  clearApiKey();
  window.location.reload();
}

/** Build a query string from key-value pairs, omitting undefined values. */
function qs(params: Record<string, string | number | undefined>): string {
  const sp = new URLSearchParams();
  for (const [k, v] of Object.entries(params)) {
    if (v !== undefined) sp.set(k, String(v));
  }
  const s = sp.toString();
  return s ? `?${s}` : '';
}

async function apiFetch<T>(path: string, init?: RequestInit): Promise<T> {
  const key = getApiKey();
  const res = await fetch(`${API_BASE}${path}`, {
    ...init,
    headers: {
      ...init?.headers,
      ...(key ? { Authorization: `Bearer ${key}` } : {}),
    },
  });
  if (res.status === 401) {
    clearApiKey();
    window.location.reload();
    throw new ApiError(401, 'Unauthorized');
  }
  if (!res.ok) {
    throw new ApiError(res.status, `API error: ${res.status} ${res.statusText}`);
  }
  return res.json();
}

// --- Types ---

export interface VideoSummaryItem {
  video_id: string;
  calls: number;
  cost_usd: number;
  tokens_in: number;
  tokens_out: number;
  first_call: string | null;
  last_call: string | null;
  title: string | null;
  channel: string | null;
  duration: number | null;
  thumbnail_url: string | null;
  status: string | null;
  category: string | null;
  processed_at: string | null;
}

export interface VideoDetailResponse {
  video: {
    title: string | null;
    channel: string | null;
    duration: number | null;
    thumbnail_url: string | null;
    status: string | null;
    category: string | null;
    processed_at: string | null;
  } | null;
  summary: {
    total_calls: number;
    total_cost_usd: number;
    total_tokens_in: number;
    total_tokens_out: number;
    avg_duration_ms: number;
    first_call: string | null;
    last_call: string | null;
  };
  by_feature: Array<{
    feature: string;
    calls: number;
    cost_usd: number;
    tokens_in: number;
    tokens_out: number;
    avg_duration_ms: number;
  }>;
  calls: Array<{
    _id: string;
    model: string;
    feature: string;
    cost_usd: number;
    tokens_in: number;
    tokens_out: number;
    duration_ms: number;
    timestamp: string;
    video_id?: string;
    service?: string;
    success?: boolean;
    /** "tokens" (default) or "audio_seconds" for transcription rows. */
    unit?: string | null;
    audio_seconds?: number | null;
  }>;
}

// --- New Analytics Types ---

export interface OutputTypeUsage {
  output_type: string;
  cost_usd: number;
  calls: number;
  tokens: number;
}

export interface ShareItem {
  title: string;
  youtubeId: string;
  shareSlug: string;
  viewsCount: number;
  likesCount: number;
  sharedAt: string | null;
  outputType: string;
}

export interface ShareStats {
  total_shared: number;
  total_views: number;
  total_likes: number;
  avg_views: number;
}

export interface TierItem {
  tier: string;
  count: number;
  percentage: number;
}

export interface UserCostRow {
  userId: string;
  email: string | null;
  name: string | null;
  tier: string;
  totalCostUsd: number;
  videoCount: number;
  creditAdjustmentUsd: number;
  effectiveUsd: number;
  days: number;
}

export interface UserDailyRow {
  date: string;
  totalCostUsd: number;
  videoCount: number;
  creditAdjustmentUsd: number;
  effectiveUsd: number;
}

export interface UserCostAdjustment {
  id: string;
  date: string;
  amountUsd: number;
  reason: string;
  adminId: string | null;
  createdAt: string | null;
}

export interface UserCostDetail {
  user: UserCostRow;
  daily: UserDailyRow[];
  adjustments: UserCostAdjustment[];
}

export interface GrantCreditResponse {
  userId: string;
  date: string;
  amountUsd: number;
  creditAdjustmentUsd: number;
  effectiveUsd: number;
}

// --- Run-grouped usage types (GET /usage/by-run) ---

export interface RunCallSummary {
  id: string;
  feature: string | null;
  model: string | null;
  cost_usd: number;
  tokens_in: number | null;
  tokens_out: number | null;
  duration_ms: number | null;
  success: boolean | null;
  timestamp: string;
  /** Cost unit: "tokens" (default) or "audio_seconds" for transcription rows. */
  unit?: string | null;
  /** Billed audio duration in seconds, for transcription rows. */
  audio_seconds?: number | null;
}

export interface RunSummary {
  request_id: string | null;
  video_id: string | null;
  video_summary_id: string | null;
  user_id: string | null;
  first_call: string;
  last_call: string;
  total_cost_usd: number;
  call_count: number;
  regen_ordinal: number | null;
  /** Direct link to this run's Langfuse trace, resolved server-side. Null when unconfigured/unresolvable. */
  langfuse_url: string | null;
  /**
   * Partial-result flag from the run's videoSummaryCache doc (dropped
   * extraction batches / critical coverage). True = degraded, false = clean,
   * null/undefined = no doc resolvable (legacy rows).
   */
  degraded?: boolean | null;
  calls: RunCallSummary[];
}

// --- User-360 activity types (GET /users/{id}/activity) ---

export interface UserVideoRow {
  userVideoId: string;
  videoSummaryId: string;
  youtubeId: string;
  title: string | null;
  channel: string | null;
  duration: number | null;
  thumbnailUrl: string | null;
  status: string | null;
  addedAt: string | null;
}

export interface UserAssistantCallRow {
  id: string;
  feature: string | null;
  videoId: string | null;
  model: string | null;
  costUsd: number;
  tokensIn: number | null;
  tokensOut: number | null;
  requestId: string | null;
  timestamp: string;
  unit?: string | null;
  audioSeconds?: number | null;
}

export interface UserCostTimelineRow {
  date: string;
  totalCostUsd: number;
  creditAdjustmentUsd: number;
  effectiveUsd: number;
}

export interface UserActivityResponse {
  userId: string;
  videos: UserVideoRow[];
  assistantCalls: UserAssistantCallRow[];
  costTimeline: UserCostTimelineRow[];
}

export interface QueueStatsResponse {
  main: {
    messages: number;
    ready: number;
    inFlight: number;
    consumers: number;
  };
  dlq: {
    messages: number;
  };
}

/** One dead-lettered job as peeked from RabbitMQ (vie-api /api/admin/queue/dlq). */
export interface DlqMessage {
  payload: Record<string, unknown> | null;
  routingKey: string | null;
  messageId: string | null;
  priority: number | null;
  headers: Record<string, unknown> | null;
}

export interface QueueDlqResponse {
  messages: DlqMessage[];
}

export interface QueueReplayResponse {
  replayed: number;
}

export interface FeatureUsageRow {
  feature: string;
  calls: number;
  cost_usd: number;
  avg_duration_ms?: number | null;
  p50_duration_ms?: number | null;
  p95_duration_ms?: number | null;
}

export interface ModelUsageRow {
  model: string;
  calls: number;
  cost_usd: number;
  tokens_in?: number;
  tokens_out?: number;
  avg_duration_ms?: number | null;
  p50_duration_ms?: number | null;
  p95_duration_ms?: number | null;
}

/** One expensive call from /usage/anomalies (raw llm_usage doc, serialized). */
export interface AnomalyRow {
  _id: string;
  model?: string | null;
  feature?: string | null;
  cost_usd?: number;
  tokens_in?: number;
  tokens_out?: number;
  duration_ms?: number;
  video_id?: string | null;
  request_id?: string | null;
  timestamp?: string;
  unit?: string | null;
  audio_seconds?: number | null;
}

/** One duplicate-prompt group from /usage/duplicates. */
export interface DuplicateRow {
  prompt_hash: string;
  count: number;
  total_cost_usd: number;
  model?: string | null;
  feature?: string | null;
  prompt_preview?: string | null;
}

export type SystemStatus = 'healthy' | 'degraded' | 'down' | 'unknown';

export interface HealthOverviewResponse {
  status: SystemStatus;
  services: Record<string, { status: string; response_ms?: number }>;
  checked_at: string;
}

// Usage endpoints
/** Result of vie-api's global video delete (every store, every user). */
export interface VideoPurgeResponse {
  scope: 'global';
  youtubeId: string;
  summaryIds: string[];
  counts: Record<string, number>;
  warnings: string[];
}

export const api = {
  usage: {
    stats: (days = 30) => apiFetch<Record<string, number>>(`/usage/stats${qs({ days })}`),
    daily: (days = 30) => apiFetch<Array<{ date: string; calls: number; cost_usd: number }>>(`/usage/daily${qs({ days })}`),
    byFeature: (days = 30) => apiFetch<FeatureUsageRow[]>(`/usage/by-feature${qs({ days })}`),
    byModel: (days = 30) => apiFetch<ModelUsageRow[]>(`/usage/by-model${qs({ days })}`),
    byService: (days = 30) => apiFetch<Array<{ service: string; calls: number; cost_usd: number }>>(`/usage/by-service${qs({ days })}`),
    byVideo: (days = 30, limit = 20) => apiFetch<VideoSummaryItem[]>(`/usage/by-video${qs({ days, limit })}`),
    forVideo: (videoId: string) => apiFetch<VideoDetailResponse>(`/usage/video/${encodeURIComponent(videoId)}`),
    anomalies: (threshold = 0.5, days = 7) => apiFetch<AnomalyRow[]>(`/usage/anomalies${qs({ threshold_usd: threshold, days })}`),
    recent: (limit = 20, beforeId?: string) => apiFetch<Array<Record<string, unknown>>>(`/usage/recent${qs({ limit, before_id: beforeId })}`),
    duplicates: (days = 7) => apiFetch<DuplicateRow[]>(`/usage/duplicates${qs({ days })}`),
    byOutputType: (days = 30) => apiFetch<OutputTypeUsage[]>(`/usage/by-output-type${qs({ days })}`),
    byRun: (days = 30, limit = 20, offset = 0) =>
      apiFetch<RunSummary[]>(`/usage/by-run${qs({ days, limit, offset })}`),
  },
  shares: {
    top: (days = 30, limit = 10) => apiFetch<ShareItem[]>(`/shares/top${qs({ days, limit })}`),
    stats: (days = 30) => apiFetch<ShareStats>(`/shares/stats${qs({ days })}`),
  },
  tiers: {
    distribution: () => apiFetch<TierItem[]>('/tiers/distribution'),
  },
  health: {
    services: () => apiFetch<Record<string, { status: string; response_ms?: number }>>('/health/services'),
    overview: () => apiFetch<HealthOverviewResponse>('/health/overview'),
    history: (hours = 24) => apiFetch<Array<Record<string, unknown>>>(`/health/history${qs({ hours })}`),
    uptime: (days = 7) => apiFetch<Record<string, { uptime_pct: number }>>(`/health/uptime${qs({ days })}`),
  },
  alerts: {
    recent: (limit = 20) => apiFetch<Array<Record<string, unknown>>>(`/alerts/recent${qs({ limit })}`),
    config: () => apiFetch<Record<string, number>>('/alerts/config'),
    updateConfig: (config: Record<string, number>) =>
      apiFetch<Record<string, unknown>>('/alerts/config', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(config),
      }),
  },
  users: {
    costs: (days = 7, limit = 50, offset = 0) =>
      apiFetch<UserCostRow[]>(`/users/costs${qs({ days, limit, offset })}`),
    costDetail: (userId: string, days = 30) =>
      apiFetch<UserCostDetail>(`/users/${encodeURIComponent(userId)}/costs${qs({ days })}`),
    grantCredit: (userId: string, body: { amountUsd: number; reason: string; adminId: string; date?: string }) =>
      apiFetch<GrantCreditResponse>(`/users/${encodeURIComponent(userId)}/grant-credit`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body),
      }),
    activity: (userId: string) =>
      apiFetch<UserActivityResponse>(`/users/${encodeURIComponent(userId)}/activity`),
  },
  queue: {
    stats: () => apiFetch<QueueStatsResponse>('/queue/stats'),
    dlq: (limit = 20) => apiFetch<QueueDlqResponse>(`/queue/dlq${qs({ limit })}`),
    replay: (max = 100) =>
      apiFetch<QueueReplayResponse>('/queue/replay', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ max }),
      }),
  },
  videos: {
    /** Delete a video from every store for every user — vie-api runs the cascade. */
    purge: (videoId: string, reason?: string) =>
      apiFetch<VideoPurgeResponse>(`/videos/${encodeURIComponent(videoId)}`, {
        method: 'DELETE',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(reason ? { reason } : {}),
      }),
  },
};
