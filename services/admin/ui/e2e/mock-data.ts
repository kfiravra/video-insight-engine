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
  // The video's pipeline attempts, newest first (/usage/video/{id} `runs`).
  runs: [
    {
      run_id: '7c1d2e3f-4a5b-4c6d-8e7f-90a1b2c3d4e5',
      request_id: 'a1b2c3d4-e5f6-4a7b-8c9d-0e1f2a3b4c5d',
      video_id: 'abc123',
      video_summary_id: '6a8ebbbfe517dd6fad90fb1d',
      user_id: 'user-1',
      first_call: '2026-02-26T13:58:00Z',
      last_call: '2026-02-26T14:00:00Z',
      total_cost_usd: 0.0412,
      call_count: 8,
      regen_ordinal: 2,
      attempt: 2,
      pipeline_version: 'v11',
      bypass: false,
      path: 'worker',
      status: 'completed',
      retry_of: 'b4e8f0a2-1c3d-4e5f-8a6b-7c8d9e0f1a2b',
      langfuse_url: 'https://cloud.langfuse.com/project/proj-e2e/traces/trace-abc-2',
      session_url: 'https://cloud.langfuse.com/project/proj-e2e/sessions/pipeline-6a8ebbbfe517dd6fad90fb1d',
      degraded: false,
      degraded_reasons: null,
      calls: [
        { id: 'call-1', model: 'claude-sonnet-4-6', feature: 'summarize', cost_usd: 0.0089, tokens_in: 1500, tokens_out: 800, duration_ms: 2100, success: true, timestamp: '2026-02-26T14:00:00Z' },
        { id: 'call-2', model: 'gpt-4o', feature: 'explain', cost_usd: 0.0045, tokens_in: 1200, tokens_out: 600, duration_ms: 1800, success: true, timestamp: '2026-02-26T13:59:00Z' },
      ],
    },
    {
      run_id: 'b4e8f0a2-1c3d-4e5f-8a6b-7c8d9e0f1a2b',
      request_id: 'a1b2c3d4-e5f6-4a7b-8c9d-0e1f2a3b4c5d',
      video_id: 'abc123',
      video_summary_id: '6a8ebbbfe517dd6fad90fb1d',
      user_id: 'user-1',
      first_call: '2026-02-26T13:50:00Z',
      last_call: '2026-02-26T13:52:00Z',
      total_cost_usd: 0.0111,
      call_count: 4,
      regen_ordinal: 1,
      attempt: 1,
      pipeline_version: 'v11',
      bypass: false,
      path: 'worker',
      status: 'failed',
      retry_of: null,
      langfuse_url: 'https://cloud.langfuse.com/project/proj-e2e/traces/trace-abc-1',
      session_url: 'https://cloud.langfuse.com/project/proj-e2e/sessions/pipeline-6a8ebbbfe517dd6fad90fb1d',
      degraded: false,
      degraded_reasons: null,
      calls: [
        { id: 'call-3', model: 'claude-haiku-4-5-20251001', feature: 'classify', cost_usd: 0.0011, tokens_in: 900, tokens_out: 120, duration_ms: 700, success: true, timestamp: '2026-02-26T13:50:30Z' },
      ],
    },
  ],
};

/** /usage/by-run: run-keyed attempts (a retry pair, a bypass, a degraded dev run), a request-keyed legacy row and the unattributed bucket. */
export const mockUsageByRun = [
  ...mockVideoDetail.runs,
  {
    run_id: 'e5f6a7b8-9c0d-4e1f-8a2b-3c4d5e6f7a8b',
    request_id: 'c3d4e5f6-a7b8-4c9d-8e0f-1a2b3c4d5e6f',
    video_id: 'def456',
    video_summary_id: '6a8ebbbfe517dd6fad90fb2e',
    user_id: 'user-2',
    first_call: '2026-02-25T16:28:00Z',
    last_call: '2026-02-25T16:30:00Z',
    total_cost_usd: 0.0312,
    call_count: 8,
    regen_ordinal: 1,
    attempt: 1,
    pipeline_version: 'v11',
    bypass: true,
    path: 'sse',
    status: 'running',
    retry_of: null,
    langfuse_url: 'https://cloud.langfuse.com/project/proj-e2e/traces/trace-def-1',
    session_url: 'https://cloud.langfuse.com/project/proj-e2e/sessions/pipeline-6a8ebbbfe517dd6fad90fb2e',
    degraded: null,
    degraded_reasons: null,
    calls: [],
  },
  {
    run_id: 'f0e1d2c3-b4a5-4968-8776-655443322110',
    request_id: 'd4c3b2a1-0f9e-4d8c-8b7a-695847362514',
    video_id: 'ghi789',
    video_summary_id: '6a8ebbbfe517dd6fad90fb3f',
    user_id: 'user-1',
    first_call: '2026-02-24T09:10:00Z',
    last_call: '2026-02-24T09:14:00Z',
    total_cost_usd: 0.0874,
    call_count: 15,
    regen_ordinal: 1,
    attempt: 1,
    pipeline_version: 'v10',
    bypass: false,
    path: 'dev',
    status: 'completed',
    retry_of: null,
    langfuse_url: 'https://cloud.langfuse.com/project/proj-e2e/traces/trace-ghi-1',
    session_url: 'https://cloud.langfuse.com/project/proj-e2e/sessions/pipeline-6a8ebbbfe517dd6fad90fb3f',
    degraded: true,
    degraded_reasons: ['media_download_failed'],
    calls: [],
  },
  {
    run_id: null,
    request_id: '0b1c2d3e-4f5a-4b6c-8d7e-8f9a0b1c2d3e',
    video_id: 'jkl012',
    video_summary_id: null,
    user_id: null,
    first_call: '2026-02-21T11:00:00Z',
    last_call: '2026-02-21T11:03:00Z',
    total_cost_usd: 0.0256,
    call_count: 6,
    regen_ordinal: 1,
    attempt: null,
    pipeline_version: null,
    bypass: null,
    path: null,
    status: null,
    retry_of: null,
    langfuse_url: null,
    session_url: null,
    calls: [],
  },
  {
    run_id: null,
    request_id: null,
    video_id: null,
    video_summary_id: null,
    user_id: null,
    first_call: '2026-02-20T08:00:00Z',
    last_call: '2026-02-20T08:40:00Z',
    total_cost_usd: 0.0049,
    call_count: 3,
    regen_ordinal: null,
    attempt: null,
    pipeline_version: null,
    bypass: null,
    path: null,
    status: null,
    retry_of: null,
    langfuse_url: null,
    session_url: null,
    calls: [],
  },
];

/** /usage/by-session: a video chat, a library chat and the pre-P1 bucket. */
export const mockUsageBySession = [
  {
    session_id: '5b1e2c3d-4f5a-4b6c-8d7e-9f0a1b2c3d4e',
    user_id: 'user-1',
    video_summary_id: '6a8ebbbfe517dd6fad90fb1d',
    turns: 3,
    tool_llm_calls: 2,
    call_count: 7,
    total_cost_usd: 0.0187,
    first_call: '2026-02-26T15:00:00Z',
    last_call: '2026-02-26T15:06:00Z',
    session_url: 'https://cloud.langfuse.com/project/proj-e2e/sessions/chat-5b1e2c3d-4f5a-4b6c-8d7e-9f0a1b2c3d4e',
  },
  {
    session_id: '8d9e0f1a-2b3c-4d4e-9f5a-6b7c8d9e0f1a',
    user_id: 'user-2',
    video_summary_id: null,
    turns: 2,
    tool_llm_calls: 1,
    call_count: 4,
    total_cost_usd: 0.0093,
    first_call: '2026-02-25T18:20:00Z',
    last_call: '2026-02-25T18:24:00Z',
    session_url: 'https://cloud.langfuse.com/project/proj-e2e/sessions/chat-8d9e0f1a-2b3c-4d4e-9f5a-6b7c8d9e0f1a',
  },
  {
    session_id: null,
    user_id: 'user-1',
    video_summary_id: null,
    turns: 14,
    tool_llm_calls: 3,
    call_count: 31,
    total_cost_usd: 0.1042,
    first_call: '2026-02-10T09:00:00Z',
    last_call: '2026-02-19T17:45:00Z',
    session_url: null,
  },
];

/** /usage/anomalies: none above the threshold. Unmocked, the live admin answers 401 and the app reloads. */
export const mockUsageAnomalies: unknown[] = [];

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
