/**
 * Smoke suite — real backend, seeded MongoDB, ZERO LLM pipeline runs.
 *
 * Unlike the rest of the e2e suite (which mocks the api via page.route),
 * these tests exercise the running stack: real auth endpoints, real
 * GET /api/videos + /api/videos/:id serving a video document seeded straight
 * into MongoDB (see helpers/smoke-seed.ts). Nothing here ever calls
 * POST /api/videos, so no summarizer pipeline (and no LLM spend) can be
 * triggered.
 *
 * Coverage:
 *   1. Login through the real /login form
 *   2. Library (/board) renders the seeded video
 *   3. /video/:id renders tabs from the cached document + tab switching
 *   4. Assistant panel opens (no message is sent — opening is free)
 *   5. Signing out and in as another user never shows the first user's library
 *
 * Requirements: api on :3000 and MongoDB on :27017 (docker-compose stack).
 * Run just this subset with: npx playwright test --project=smoke
 */
import { test, expect, type Page } from "@playwright/test";
import {
  requireSeedFile,
  SMOKE_USER,
  SMOKE_USER_B,
  SMOKE_VIDEO_TITLE,
  SMOKE_VIDEO_PATH,
  SMOKE_TAB_LABELS,
  SMOKE_CHECKLIST_ITEM,
  type SmokeSeedResult,
} from "./helpers/smoke-seed";

let seed: SmokeSeedResult;

// Seeding happens ONCE per run in smoke.setup.ts (the smoke-setup project).
// beforeAll runs per worker, and per-worker logins would trip the api's
// IP-keyed login rate limit (10/15min) — so here we only read the handoff.
test.beforeAll(() => {
  seed = requireSeedFile();
});

test.use({ viewport: { width: 1440, height: 900 } });

/**
 * Authenticate the page with the REAL access token from the seed login.
 * Mirrors exactly what the app persists after a real login:
 *  - "vie-auth": the auth-store Zustand persist blob (checkAuth() then
 *    re-validates the token against the real /api/auth/me), AND
 *  - "accessToken": the separate key api/client.ts getAccessToken() reads to
 *    build Authorization headers. Without it the first data requests go out
 *    unauthenticated, 401, and the failed-refresh path force-logs-out.
 */
async function injectAuth(page: Page): Promise<void> {
  const state = {
    state: { accessToken: seed.accessToken, user: seed.user },
    version: 0,
  };
  await page.addInitScript((authState) => {
    localStorage.setItem("vie-auth", JSON.stringify(authState));
    localStorage.setItem("accessToken", authState.state.accessToken);
  }, state);
}

test.describe("Smoke — real stack with seeded MongoDB", () => {
  test("should land on the board after logging in through the login form", async ({
    page,
  }) => {
    const loginCalls: string[] = [];
    page.on("request", (req) => {
      if (req.method() === "POST" && req.url().endsWith("/auth/login")) loginCalls.push(req.url());
    });
    await page.goto("/login");

    await page.getByLabel("Email").fill(SMOKE_USER.email);
    await page.getByLabel("Password", { exact: true }).fill(SMOKE_USER.password);
    // An impatient double click is still one login: the button disables on
    // the first click and stays disabled until the board replaces the page.
    const signIn = page.getByRole("button", { name: /sign in|signing in/i });
    await signIn.dblclick();
    await expect(signIn).toBeDisabled();

    await page.waitForURL("**/board", { timeout: 15000 });
    await expect(page).toHaveURL(/\/board$/);
    // The session was awaited (/auth/me answered) before the navigation, so
    // the board renders this user's library, not a spinner or a bounce.
    await expect(page.getByText(SMOKE_VIDEO_TITLE).first()).toBeVisible({
      timeout: 15000,
    });
    expect(loginCalls).toHaveLength(1);
  });

  test("should render the library with the seeded video", async ({ page }) => {
    await injectAuth(page);
    await page.goto("/board");

    await expect(page.getByText(SMOKE_VIDEO_TITLE).first()).toBeVisible({
      timeout: 15000,
    });
  });

  test("should render the video page tabs from the cached document", async ({
    page,
  }) => {
    await injectAuth(page);
    await page.goto(SMOKE_VIDEO_PATH);

    const tablist = page.getByRole("tablist", {
      name: "Video insight sections",
    });
    await expect(tablist).toBeVisible({ timeout: 15000 });

    // Overview is owned by the hero and filtered from the strip; the three
    // seeded content tabs must all be present.
    await expect(
      tablist.getByRole("tab", { name: SMOKE_TAB_LABELS.moments }),
    ).toBeVisible();
    await expect(
      tablist.getByRole("tab", { name: SMOKE_TAB_LABELS.checklist }),
    ).toBeVisible();
    await expect(
      tablist.getByRole("tab", { name: SMOKE_TAB_LABELS.concepts }),
    ).toBeVisible();
  });

  test("should switch tabs and render the checklist content", async ({
    page,
  }) => {
    await injectAuth(page);
    await page.goto(SMOKE_VIDEO_PATH);

    const tablist = page.getByRole("tablist", {
      name: "Video insight sections",
    });
    await expect(tablist).toBeVisible({ timeout: 15000 });

    await tablist
      .getByRole("tab", { name: SMOKE_TAB_LABELS.checklist })
      .click();

    await expect(page.getByText(SMOKE_CHECKLIST_ITEM)).toBeVisible();
  });

  test("should never show the previous user's library after switching users", async ({
    page,
  }) => {
    if (!seed.userB) {
      throw new Error("Smoke seed predates the second user: rerun the smoke project to reseed.");
    }
    await injectAuth(page);
    await page.goto("/board");
    await expect(page.getByText(SMOKE_VIDEO_TITLE).first()).toBeVisible({
      timeout: 15000,
    });

    // Sign out through the header menu, the way a user does.
    await page.getByRole("button", { name: seed.user.name, exact: true }).click();
    await page.getByRole("menuitem", { name: "Sign out" }).click();
    await page.getByRole("button", { name: "Log out" }).click();
    await page.waitForURL("**/login", { timeout: 15000 });

    // Sign-out leaves nothing of the first user behind on the client.
    const leftovers = await page.evaluate(() => ({
      accessToken: localStorage.getItem("accessToken"),
      auth: JSON.parse(localStorage.getItem("vie-auth") ?? "{}").state ?? {},
      ui: JSON.parse(localStorage.getItem("vie-ui-store") ?? "{}").state ?? {},
    }));
    expect(leftovers.accessToken).toBeNull();
    expect(leftovers.auth.user ?? null).toBeNull();
    expect(leftovers.ui.expandedFolderIds ?? []).toEqual([]);

    await page.getByLabel("Email").fill(SMOKE_USER_B.email);
    await page.getByLabel("Password", { exact: true }).fill(SMOKE_USER_B.password);
    await page.getByRole("button", { name: "Sign in" }).click();
    await page.waitForURL("**/board", { timeout: 15000 });

    // B's board settles on its own empty library; A's video is nowhere.
    await expect(page.getByText("Your library is empty")).toBeVisible({ timeout: 15000 });
    await expect(page.getByText(SMOKE_VIDEO_TITLE)).toHaveCount(0);
    const signedIn = await page.evaluate(
      () => JSON.parse(localStorage.getItem("vie-auth") ?? "{}").state?.user?.email,
    );
    expect(signedIn).toBe(SMOKE_USER_B.email);
  });

  test("should open the assistant panel from the sidebar", async ({ page }) => {
    await injectAuth(page);
    await page.goto("/board");

    const sidebar = page.locator("aside");
    await expect(sidebar.getByText("Assistant")).toBeVisible({
      timeout: 15000,
    });
    await sidebar.getByText("Assistant").click();

    // The RAG chat panel input renders once the assistant tab is active.
    // We do NOT send a message — sending would call the assistant LLM.
    await expect(
      sidebar.getByRole("textbox", {
        name: "Ask a question across your videos...",
      }),
    ).toBeVisible();
  });
});
