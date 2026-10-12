import { test as base, expect as baseExpect, type Page, type Route } from "@playwright/test";
import { test, expect } from "./fixtures";
import {
  observeTabDom,
  scriptSseStream,
  tapSseStream,
  type InstrumentedWindow,
} from "./helpers/stream-instrumentation";

/**
 * Progressive output (pipeline-1min 2.2): the first tab appears before
 * `complete`, and the re-emits (provisional → real, non-final → final, the
 * DB doc swapped in at `done`) never remount a tab or the panel.
 *
 * Mocked (default): a scripted SSE stream shaped like the T1dQhQAm8Tc dev run
 * on c57f024 — the provisional Moments tab at plan-done, the assembly tabs
 * non-final (a quiz is pending), the framed Moments tab, the final re-emit,
 * `complete {tabIds}`, then `done` with the DB doc's frame URLs re-signed.
 *
 * Live (opt-in, a real pipeline run with LLM spend):
 *   E2E_LIVE=1 E2E_BASE_URL=http://localhost:5174 E2E_EMAIL=… E2E_PASSWORD=… E2E_YTID=…
 *   npx playwright test e2e/progressive-output.spec.ts --project=chromium -g live
 */

const VIDEO_ID = "video-progressive";
const SUMMARY_ID = "summary-progressive";
const FRAME_HOST = "https://frames.e2e.test";
const ONE_PIXEL_PNG =
  "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg==";

function frameUrl(n: number, signature: string): string {
  return `${FRAME_HOST}/videos/t1dq/frame_${n}.jpg?X-Amz-Signature=${signature}`;
}

/** Moment items: the plan-done outline (no frames), or the framed final ones. */
function momentItems(signature: string | null): Array<Record<string, unknown>> {
  return [5, 95, 240, 410].map((seconds, i) => ({
    time: `${Math.floor(seconds / 60)}:${String(seconds % 60).padStart(2, "0")}`,
    seconds,
    label: `Moment ${i + 1}`,
    ...(signature ? { s3Key: `videos/t1dq/frame_${i}.jpg`, thumbnailUrl: frameUrl(i, signature) } : {}),
  }));
}

function tabEntry(id: string, label: string, component: string, props: Record<string, unknown>) {
  return { id, label, emoji: "•", component, props, crossTabLinks: [] };
}

/** The saved response, in order — what `complete.tabIds` and the DB doc hold. */
function savedTabs(signature: string) {
  return [
    tabEntry("overview", "Overview", "overview", { brief: "A talk about the stack." }),
    tabEntry("stack_overview", "Stack overview", "display_section", { data: { tools: 10 } }),
    tabEntry("workflow_steps", "Workflow steps", "display_section", { data: { steps: 4 } }),
    tabEntry("moments", "Moments", "moment_track", { items: momentItems(signature) }),
    tabEntry("quiz", "Quiz", "display_section", { data: { questions: 5 } }),
  ];
}

const PLANNED_LABELS = [
  { id: "stack_overview", label: "Stack overview", emoji: "•" },
  { id: "workflow_steps", label: "Workflow steps", emoji: "•" },
  { id: "moments", label: "Moments", emoji: "•" },
  { id: "quiz", label: "Quiz", emoji: "•" },
];

function tabReady(tab: ReturnType<typeof tabEntry>, position: number, flags: Record<string, unknown> = {}) {
  return { event: "tab_ready", ...tab, position, final: false, ...flags };
}

interface DetailState {
  completed: boolean;
}

async function routeApi(page: Page, detail: DetailState): Promise<void> {
  // Most-recent-first: these win over the fixture's broader handlers. An
  // empty list keeps the processing manager from attaching to the stream.
  await page.route(/\/api\/videos(\?[^/]*)?$/, (route: Route) =>
    route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({ videos: [] }) }),
  );
  await page.route(/\/api\/videos\/video-progressive$/, (route: Route) =>
    route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({
        id: VIDEO_ID,
        videoSummaryId: SUMMARY_ID,
        youtubeId: "T1dQhQAm8Tc",
        title: "Progressive output video",
        creator: "E2E",
        duration: 1200,
        thumbnailUrl: null,
        status: detail.completed ? "completed" : "processing",
        folderId: null,
        meta: detail.completed ? { videoTitle: "Progressive output video", contentTags: ["tech"], primaryTag: "tech" } : null,
        // Every DB serve re-signs frame URLs (api refresh-frame-urls.ts).
        tabs: detail.completed ? savedTabs("serve") : null,
      }),
    }),
  );
  await page.route(`${FRAME_HOST}/**`, (route: Route) =>
    route.fulfill({ status: 200, contentType: "image/png", body: Buffer.from(ONE_PIXEL_PNG, "base64") }),
  );
}

async function push(page: Page, ...events: Array<Record<string, unknown>>): Promise<void> {
  await page.evaluate((batch) => {
    const w = window as unknown as InstrumentedWindow;
    for (const event of batch) w.__vieSse.push(event);
  }, events);
}

function strip(page: Page) {
  return page.getByRole("tablist", { name: "Video insight sections" });
}

test.describe("Progressive output — scripted stream", () => {
  test("should show the first tab before complete and never remount it across the re-emits", async ({
    authenticatedPage: page,
  }) => {
    const detail: DetailState = { completed: false };
    await routeApi(page, detail);
    await page.addInitScript(observeTabDom);
    await page.addInitScript(scriptSseStream);
    await page.goto(`/video/${VIDEO_ID}`);
    await page.waitForFunction(() => (window as unknown as InstrumentedWindow).__vieSse?.opened === true);

    const saved = savedTabs("run");
    await push(
      page,
      { event: "metadata", title: "Progressive output video", channel: "E2E", duration: 1200 },
      { event: "triage_complete", contentTags: ["tech"], primaryTag: "tech", confidence: 0.9, tabs: [] },
      { event: "meta", title: "Progressive output video", primaryTag: "tech", tabCount: 4, tabLabels: PLANNED_LABELS },
      { event: "synthesis_complete", tldr: "The short version.", keyTakeaways: ["One", "Two"] },
      // Plan-done: the provisional Moments tab, outline-based, no frames.
      tabReady(tabEntry("moments", "Moments", "moment_track", { items: momentItems(null) }), 3, { provisional: true }),
    );

    // First tab before `complete`, with the in-progress chrome still there.
    const moments = strip(page).getByRole("tab", { name: /moments/i });
    await expect(moments).toBeVisible();
    await expect(page.getByText("Early preview", { exact: true })).toBeVisible();
    await expect(page.getByRole("button", { name: "Cancel" })).toBeVisible();
    await expect(page.getByRole("list", { name: "Processing stages" })).toBeVisible();
    await expect(page.locator("[data-stream-tab-pill]")).toHaveCount(3);
    await page.evaluate(() => {
      (window as unknown as { __viePanel: Element | null }).__viePanel = document.querySelector('[role="tabpanel"]');
    });

    // Assembly (quiz pending → non-final), then the framed Moments tab.
    await push(page, tabReady(saved[0], 0), tabReady(saved[1], 1), tabReady(saved[2], 2));
    await expect(strip(page).getByRole("tab")).toHaveCount(3);
    await expect(moments).toHaveAttribute("aria-selected", "true");
    await push(page, tabReady(saved[3], 3));
    await expect(page.getByText("Early preview", { exact: true })).toHaveCount(0);

    // Late group, the final re-emit, complete, then done with the DB doc.
    await push(
      page,
      { event: "synthesis_complete", tldr: "The short version.", keyTakeaways: ["One", "Two"], masterSummary: "Long." },
      { event: "enrichment_complete", quiz: [] },
      ...saved.map((tab, i) => tabReady(tab, i, { final: true })),
      { event: "complete", tabCount: 5, tabIds: saved.map((t) => t.id), processingTimeMs: 138600 },
    );
    await expect(strip(page).getByRole("tab")).toHaveCount(4);
    detail.completed = true;
    await push(page, { event: "done", processingTimeMs: 138600 });
    await page.evaluate(() => (window as unknown as InstrumentedWindow).__vieSse.close());
    await expect(page.getByRole("button", { name: "Cancel" })).toHaveCount(0);

    const after = await page.evaluate(() => {
      const w = window as unknown as InstrumentedWindow & { __viePanel: Element | null };
      return {
        panelKept: w.__viePanel?.isConnected === true,
        removed: w.__vieDom.removed,
        frame: document.querySelector('[role="tabpanel"] img')?.getAttribute("src") ?? null,
      };
    });
    expect(after.panelKept).toBe(true);
    expect(after.removed).toEqual([]);
    expect(after.frame).toBe(frameUrl(0, "run"));
    await expect(strip(page).getByRole("tab", { selected: true })).toHaveAccessibleName(/moments/i);
    await expect(strip(page).getByRole("tab")).toHaveText([/stack overview/i, /workflow steps/i, /moments/i, /quiz/i]);
  });
});

// ─── Live: a real pipeline run on the dev stack (opt-in) ───

const LIVE = process.env.E2E_LIVE === "1";
const LIVE_PIPELINE_TIMEOUT_MS = 10 * 60_000;

base.describe("Progressive output — live dev run", () => {
  base.skip(!LIVE, "set E2E_LIVE=1 (+ E2E_BASE_URL, E2E_EMAIL, E2E_PASSWORD, E2E_YTID) to run a real pipeline");
  base.use(process.env.E2E_BASE_URL ? { baseURL: process.env.E2E_BASE_URL } : {});

  base("live: first tab before complete, no tab or panel remounts through done", async ({ page, request, baseURL }) => {
    base.setTimeout(LIVE_PIPELINE_TIMEOUT_MS + 60_000);
    const api = `${baseURL}/api`;
    const login = await request.post(`${api}/auth/login`, {
      data: { email: process.env.E2E_EMAIL, password: process.env.E2E_PASSWORD },
    });
    baseExpect(login.status(), "login failed — check E2E_EMAIL / E2E_PASSWORD").toBe(200);
    const auth = (await login.json()) as { accessToken: string; user: Record<string, unknown> };

    await page.addInitScript((state) => {
      localStorage.setItem("vie-auth", JSON.stringify({ state, version: 0 }));
      localStorage.setItem("accessToken", state.accessToken);
    }, { accessToken: auth.accessToken, user: auth.user });
    await page.addInitScript(observeTabDom);
    await page.addInitScript(tapSseStream);

    const created = await request.post(`${api}/videos`, {
      headers: { Authorization: `Bearer ${auth.accessToken}` },
      data: { url: `https://www.youtube.com/watch?v=${process.env.E2E_YTID}`, bypassCache: true },
    });
    const body = (await created.json()) as { video?: { id: string; status?: string }; cached?: boolean };
    baseExpect(created.status(), `expected a fresh run (201), got ${created.status()}`).toBe(201);
    baseExpect(body.cached, "the API served a cached result — the run must be fresh").toBeFalsy();

    await page.goto(`/video/${body.video?.id}`);
    await page.waitForFunction(
      () => (window as unknown as InstrumentedWindow).__vieSseLog?.some((e) => e.event === "done"),
      null,
      { timeout: LIVE_PIPELINE_TIMEOUT_MS, polling: 1000 },
    );
    // The page refetches the doc on done; give the DB swap time to land.
    await page.waitForTimeout(3000);

    const run = await page.evaluate(() => {
      const w = window as unknown as InstrumentedWindow;
      return { dom: w.__vieDom, sse: w.__vieSseLog };
    });
    const completeAt = run.sse.find((e) => e.event === "complete")?.t ?? null;
    const finalIds = new Set(run.sse.find((e) => e.event === "complete")?.tabIds ?? []);
    const filmstripIds = new Set(run.sse.filter((e) => e.component === "video_filmstrip").map((e) => e.id));
    // Legit removals: a withdrawn tab (missing from complete.tabIds) and the
    // filmstrip the strip suppresses once Moments has rich frames.
    const unexpected = run.dom.removed.filter((r) => {
      const id = r.id.replace(/^(tab|panel)-/, "");
      return finalIds.has(id) && !filmstripIds.has(id);
    });
    await base.info().attach("live-timeline.json", {
      body: JSON.stringify({ firstTabAt: run.dom.firstTabAt, completeAt, removed: run.dom.removed, events: run.sse.length }, null, 2),
      contentType: "application/json",
    });

    baseExpect(completeAt, "the stream never sent complete").not.toBeNull();
    baseExpect(run.dom.firstTabAt, "no tab ever appeared").not.toBeNull();
    baseExpect(run.dom.firstTabAt ?? Infinity).toBeLessThan(completeAt ?? 0);
    baseExpect(unexpected).toEqual([]);
  });
});
