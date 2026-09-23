/**
 * Shared mock data for E2E tests.
 * Separated from fixtures to keep test setup focused on route interception.
 */

export const mockUsageStats = {
  total_cost_usd: 12.4567,
  total_calls: 1247,
  avg_duration_ms: 2340,
  success_count: 1199.614, // 1247 * 0.962 ~ 1199.614 -> 96.2%
};

export const mockUsageDaily = [
  { date: '2026-02-20', calls: 150, cost_usd: 1.23 },
  { date: '2026-02-21', calls: 180, cost_usd: 1.56 },
  { date: '2026-02-22', calls: 200, cost_usd: 2.01 },
  { date: '2026-02-23', calls: 170, cost_usd: 1.45 },
  { date: '2026-02-24', calls: 190, cost_usd: 1.78 },
  { date: '2026-02-25', calls: 210, cost_usd: 2.34 },
  { date: '2026-02-26', calls: 147, cost_usd: 2.07 },
];

export const mockUsageByFeature = [
  { feature: 'summarize', calls: 520, cost_usd: 5.12 },
  { feature: 'explain', calls: 430, cost_usd: 4.89 },
  { feature: 'chat', calls: 297, cost_usd: 2.44 },
];

export const mockUsageByModel = [
  { model: 'claude-sonnet-4-6', calls: 600, cost_usd: 6.78 },
  { model: 'gpt-4o', calls: 400, cost_usd: 3.89 },
  { model: 'claude-haiku-4-5-20251001', calls: 247, cost_usd: 1.78 },
];

export const mockUsageByService = [
  { service: 'summarizer', calls: 700, cost_usd: 8.12 },
  { service: 'explainer', calls: 547, cost_usd: 4.34 },
];

export const mockUsageRecent = [
  {
    _id: 'rec-1',
    model: 'claude-sonnet-4-6',
    feature: 'summarize',
    cost_usd: 0.0234,
    tokens_in: 1500,
    tokens_out: 800,
    duration_ms: 2100,
    timestamp: '2026-02-26T10:30:00Z',
  },
  {
    _id: 'rec-2',
    model: 'gpt-4o',
    feature: 'explain',
    cost_usd: 0.0156,
    tokens_in: 1200,
    tokens_out: 600,
    duration_ms: 1800,
    timestamp: '2026-02-26T10:25:00Z',
  },
  {
    _id: 'rec-3',
    model: 'claude-haiku-4-5-20251001',
    feature: 'chat',
    cost_usd: 0.0089,
    tokens_in: 800,
    tokens_out: 400,
    duration_ms: 950,
    timestamp: '2026-02-26T10:20:00Z',
  },
];

export const mockUsageByVideo = [
  {
    video_id: 'abc123',
    calls: 12,
    cost_usd: 0.0523,
    tokens_in: 15000,
    tokens_out: 8000,
    first_call: '2026-02-20T10:30:00Z',
    last_call: '2026-02-26T14:00:00Z',
    title: 'Building a REST API with FastAPI',
    channel: 'Tech Channel',
    duration: 752,
    thumbnail_url: null,
    status: 'completed',
    category: 'coding',
    processed_at: '2026-02-20T10:35:00Z',
  },
  {
    video_id: 'def456',
    calls: 8,
    cost_usd: 0.0312,
    tokens_in: 9500,
    tokens_out: 5200,
    first_call: '2026-02-22T08:00:00Z',
    last_call: '2026-02-25T16:30:00Z',
    title: 'Understanding React Server Components',
    channel: 'Frontend Masters',
    duration: 1215,
    thumbnail_url: null,
    status: 'completed',
    category: 'frontend',
    processed_at: '2026-02-22T08:05:00Z',
  },
];

export const mockVideoDetail = {
  video: {
    title: 'Building a REST API with FastAPI',
    channel: 'Tech Channel',
    duration: 752,
    thumbnail_url: null,
    status: 'completed',
    category: 'coding',
    processed_at: '2026-02-20T10:35:00Z',
  },
  summary: {
    total_calls: 12,
    total_cost_usd: 0.0523,
    total_tokens_in: 15000,
    total_tokens_out: 8000,
    avg_duration_ms: 2100,
    first_call: '2026-02-20T10:30:00Z',
    last_call: '2026-02-26T14:00:00Z',
  },
  by_feature: [
    { feature: 'summarize', calls: 8, cost_usd: 0.0389, tokens_in: 10000, tokens_out: 5500, avg_duration_ms: 2300 },
    { feature: 'explain', calls: 4, cost_usd: 0.0134, tokens_in: 5000, tokens_out: 2500, avg_duration_ms: 1800 },
  ],
  calls: [
    { _id: 'call-1', model: 'claude-sonnet-4-6', feature: 'summarize', cost_usd: 0.0089, tokens_in: 1500, tokens_out: 800, duration_ms: 2100, timestamp: '2026-02-26T14:00:00Z' },
    { _id: 'call-2', model: 'gpt-4o', feature: 'explain', cost_usd: 0.0045, tokens_in: 1200, tokens_out: 600, duration_ms: 1800, timestamp: '2026-02-26T12:00:00Z' },
  ],
};

export const mockUsageDuplicates = [
  { video_id: 'dup-vid-1', model: 'claude-sonnet-4-6', count: 3, total_cost_usd: 0.0702 },
];

export const mockHealthServices = {
  api: { status: 'healthy', response_ms: 45 },
  summarizer: { status: 'healthy', response_ms: 120 },
  explainer: { status: 'degraded', response_ms: 3200 },
  mongodb: { status: 'healthy', response_ms: 8 },
};

export const mockHealthOverview = {
  status: 'degraded',
  services: mockHealthServices,
};

export const mockHealthUptime = {
  api: { uptime_pct: 99.85 },
  summarizer: { uptime_pct: 98.21 },
  explainer: { uptime_pct: 95.5 },
  mongodb: { uptime_pct: 100 },
};

export const mockAlertsRecent = [
  {
    _id: 'alert-1',
    type: 'high_cost',
    model: 'claude-sonnet-4-6',
    feature: 'summarize',
    cost_usd: 0.8923,
    timestamp: '2026-02-26T09:00:00Z',
  },
  {
    _id: 'alert-2',
    type: 'spike',
    model: 'gpt-4o',
    feature: 'explain',
    cost_usd: 0.4512,
    timestamp: '2026-02-26T08:30:00Z',
  },
  {
    _id: 'alert-3',
    type: 'failure',
    model: 'claude-haiku-4-5-20251001',
    feature: 'chat',
    cost_usd: 0.0,
    timestamp: '2026-02-26T08:00:00Z',
  },
];

export const mockAlertConfig = {
  cost_threshold_usd: 0.5,
  daily_spike_multiplier: 2,
  failure_rate_threshold: 0.2,
};

export const mockUsageByOutputType = [
  { output_type: 'summary', cost_usd: 5.23, calls: 450, tokens: 125000 },
  { output_type: 'tutorial', cost_usd: 3.12, calls: 280, tokens: 89000 },
  { output_type: 'recipe', cost_usd: 1.89, calls: 150, tokens: 42000 },
  { output_type: 'review', cost_usd: 0.78, calls: 67, tokens: 18500 },
];

export const mockSharesTop = [
  { title: 'How to Make Sourdough Bread', youtubeId: 'bread123', shareSlug: 'sourdough-guide', viewsCount: 1523, likesCount: 89, sharedAt: '2026-02-20T10:00:00Z', outputType: 'recipe' },
  { title: 'React Hooks Deep Dive', youtubeId: 'hooks456', shareSlug: 'react-hooks', viewsCount: 982, likesCount: 45, sharedAt: '2026-02-22T14:30:00Z', outputType: 'tutorial' },
  { title: 'Morning Yoga Flow', youtubeId: 'yoga789', shareSlug: 'yoga-flow', viewsCount: 654, likesCount: 32, sharedAt: '2026-02-25T08:00:00Z', outputType: 'workout' },
];

export const mockSharesStats = {
  total_shared: 47,
  total_views: 12450,
  total_likes: 623,
  avg_views: 264.9,
};

export const mockTierDistribution = [
  { tier: 'free', count: 850, percentage: 72.6 },
  { tier: 'pro', count: 280, percentage: 23.9 },
  { tier: 'team', count: 41, percentage: 3.5 },
];

export const mockVideoPurge = {
  scope: 'global',
  youtubeId: 'abc123',
  summaryIds: ['6a8ebbbfe517dd6fad90fb1d'],
  counts: { videoSummaryCache: 1, userVideos: 2, qdrantPoints: 58, s3Objects: 41, redisKeys: 1 },
  warnings: [],
};
