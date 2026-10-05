import { http, HttpResponse } from "msw";

const API_URL = "http://localhost:3000/api";

// Mock data factories
export const createMockUser = (overrides = {}) => ({
  id: "user-1",
  email: "test@example.com",
  name: "Test User",
  ...overrides,
});

export const createMockVideo = (overrides = {}) => ({
  id: "video-1",
  youtubeId: "abc123",
  title: "Test Video",
  channelTitle: "Test Channel",
  thumbnailUrl: "https://example.com/thumb.jpg",
  duration: 300,
  processingStatus: "completed" as const,
  folderId: null,
  createdAt: new Date().toISOString(),
  updatedAt: new Date().toISOString(),
  ...overrides,
});

export const createMockVideoSummary = (overrides = {}) => ({
  id: "summary-1",
  videoId: "video-1",
  overview: "This is a test summary",
  chapters: [],
  concepts: [],
  createdAt: new Date().toISOString(),
  ...overrides,
});

export const createMockFolder = (overrides = {}) => ({
  id: "folder-1",
  name: "Test Folder",
  parentId: null,
  color: null,
  icon: null,
  userId: "user-1",
  createdAt: new Date().toISOString(),
  updatedAt: new Date().toISOString(),
  ...overrides,
});

// Default handlers - override in individual tests as needed
export const handlers = [
  // Auth endpoints
  http.post(`${API_URL}/auth/register`, async ({ request }) => {
    const body = (await request.json()) as {
      email: string;
      password: string;
      name: string;
    };
    return HttpResponse.json({
      user: createMockUser({ email: body.email, name: body.name }),
      accessToken: "test-access-token",
    });
  }),

  http.post(`${API_URL}/auth/login`, async ({ request }) => {
    const body = (await request.json()) as {
      email?: string;
      password?: string;
      demo?: boolean;
    };
    const user = body.demo
      ? createMockUser({ id: "demo-user", email: "demo@example.com", name: "Demo" })
      : createMockUser({ email: body.email });
    return HttpResponse.json({ user, accessToken: "test-access-token" });
  }),

  http.post(`${API_URL}/auth/logout`, () => {
    return new HttpResponse(null, { status: 204 });
  }),

  http.post(`${API_URL}/auth/refresh`, () => {
    return HttpResponse.json({ accessToken: "refreshed-token" });
  }),

  http.get(`${API_URL}/auth/me`, () => {
    return HttpResponse.json(createMockUser());
  }),

  http.get(`${API_URL}/auth/registration`, () => {
    return HttpResponse.json({ open: true });
  }),

  // Video endpoints
  http.get(`${API_URL}/videos`, ({ request }) => {
    const url = new URL(request.url);
    const folderId = url.searchParams.get("folderId");

    const videos = [
      createMockVideo({ id: "video-1" }),
      createMockVideo({ id: "video-2", title: "Second Video" }),
    ];

    const filtered = folderId
      ? videos.filter((v) => v.folderId === folderId)
      : videos;

    return HttpResponse.json({
      videos: filtered,
      total: filtered.length,
      hasMore: false,
    });
  }),

  http.get(`${API_URL}/videos/:id`, ({ params }) => {
    const { id } = params;
    return HttpResponse.json({
      video: createMockVideo({ id: id as string }),
      summary: createMockVideoSummary({ videoId: id as string }),
    });
  }),

  http.post(`${API_URL}/videos`, async ({ request }) => {
    const body = (await request.json()) as {
      url: string;
      folderId?: string;
    };
    return HttpResponse.json({
      video: createMockVideo({ folderId: body.folderId }),
      cached: false,
    });
  }),

  http.delete(`${API_URL}/videos/:id`, () => {
    return new HttpResponse(null, { status: 204 });
  }),

  http.patch(`${API_URL}/videos/:id/move`, async ({ request, params }) => {
    const body = (await request.json()) as { folderId: string | null };
    return HttpResponse.json({
      success: true,
      video: createMockVideo({
        id: params.id as string,
        folderId: body.folderId,
      }),
    });
  }),

  // Folder endpoints
  http.get(`${API_URL}/folders`, () => {
    const folders = [
      createMockFolder({ id: "folder-1", name: "Folder 1" }),
      createMockFolder({ id: "folder-2", name: "Folder 2" }),
    ];

    return HttpResponse.json({ folders });
  }),

  http.get(`${API_URL}/folders/:id`, ({ params }) => {
    return HttpResponse.json(
      createMockFolder({ id: params.id as string })
    );
  }),

  http.post(`${API_URL}/folders`, async ({ request }) => {
    const body = (await request.json()) as { name: string };
    return HttpResponse.json(
      createMockFolder({ name: body.name })
    );
  }),

  http.patch(`${API_URL}/folders/:id`, async ({ request, params }) => {
    const body = (await request.json()) as { name?: string };
    return HttpResponse.json(
      createMockFolder({ id: params.id as string, ...body })
    );
  }),

  http.delete(`${API_URL}/folders/:id`, () => {
    return new HttpResponse(null, { status: 204 });
  }),
];
