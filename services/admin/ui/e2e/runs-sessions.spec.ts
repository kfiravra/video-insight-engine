import { test, expect } from './fixtures';
import { mockUsageByRun, mockUsageBySession, mockVideoDetail } from './mock-data';
import type { Locator, Page, TestInfo } from '@playwright/test';

/**
 * Set E2E_SCREENSHOT_DIR to save review screenshots of the three views
 * (desktop and mobile projects only), e.g. for a phase gate.
 */
const SCREENSHOT_DIR = process.env.E2E_SCREENSHOT_DIR;

const [RETRY_RUN, FIRST_ATTEMPT] = mockVideoDetail.runs;
const VIDEO_SESSION_URL = mockUsageBySession[0].session_url ?? '';

function panel(page: Page, title: string | RegExp): Locator {
  return page
    .locator('[data-slot="panel"]')
    .filter({ has: page.locator('[data-slot="panel-title"]', { hasText: title }) });
}

/** A full-page screenshot, clipped to `clipTo` when given (the Usage page panels). */
async function shoot(page: Page, name: string, testInfo: TestInfo, clipTo?: Locator) {
  if (!SCREENSHOT_DIR || testInfo.project.name === 'tablet') return;
  // From the top, or the sticky header is painted wherever the last click scrolled to.
  await page.evaluate(() => window.scrollTo(0, 0));
  const clip = clipTo ? await clipTo.boundingBox() : null;
  await page.screenshot({
    path: `${SCREENSHOT_DIR}/${name}-${testInfo.project.name}.png`,
    fullPage: true,
    ...(clip ? { clip } : {}),
  });
}

test.describe('Usage Page — pipeline runs', () => {
  test('should list attempts by run id with status, retries and Langfuse links', async ({ adminPage }, testInfo) => {
    await adminPage.goto('/usage');
    const runs = panel(adminPage, /^Pipeline Runs$/);
    await expect(runs).toBeVisible();

    const rows = runs.locator('tbody > tr');
    await expect(rows).toHaveCount(mockUsageByRun.length);

    // The retry: its run id, the run it retried, a completed status and both links.
    const retry = rows.first();
    await expect(retry.getByText('7c1d2e…d4e5')).toBeVisible();
    await expect(retry.getByText('retry of b4e8f0…1a2b')).toBeVisible();
    await expect(retry.getByText('completed')).toBeVisible();
    await expect(retry.getByRole('link', { name: /^Open trace of run .+ in Langfuse/ })).toHaveAttribute('href', RETRY_RUN.langfuse_url);
    await expect(retry.getByRole('link', { name: /^Open session of run .+ in Langfuse/ })).toHaveAttribute('href', RETRY_RUN.session_url);
    await expect(rows.nth(1).getByText('failed')).toBeVisible();
    await expect(rows.nth(1).getByRole('link', { name: /^Open trace of run .+ in Langfuse/ })).toHaveAttribute('href', FIRST_ATTEMPT.langfuse_url);

    // Legacy rows: one keyed by its request id, then the unattributed bucket; neither has links.
    const requestKeyed = rows.nth(4);
    const unattributed = rows.nth(5);
    await expect(requestKeyed.getByText('0b1c2d…2d3e')).toBeVisible();
    await expect(unattributed.getByText('unattributed (legacy)')).toBeVisible();
    await expect(requestKeyed.getByRole('link', { name: /in Langfuse/ })).toHaveCount(0);
    await expect(unattributed.getByRole('link', { name: /in Langfuse/ })).toHaveCount(0);

    await retry.getByRole('button', { name: 'Expand' }).click();
    await expect(runs.getByText('claude-sonnet-4-6')).toBeVisible();

    await shoot(adminPage, 'p2-runs', testInfo, runs);
  });
});

test.describe('Usage Page — chat sessions', () => {
  test('should list chat sessions after the runs, with surface and Langfuse link', async ({ adminPage }, testInfo) => {
    await adminPage.goto('/usage');
    const sessions = panel(adminPage, /^Chat Sessions$/);
    await expect(sessions).toBeVisible();

    // Placed right after the Pipeline Runs panel.
    const titles = await adminPage.locator('[data-slot="panel-title"]').allTextContents();
    expect(titles.indexOf('Chat Sessions')).toBe(titles.indexOf('Pipeline Runs') + 1);

    const rows = sessions.locator('tbody > tr');
    await expect(rows).toHaveCount(mockUsageBySession.length);
    await expect(rows.nth(0).getByText('5b1e2c…3d4e')).toBeVisible();
    await expect(rows.nth(1).getByText('8d9e0f…0f1a')).toBeVisible();
    await expect(rows.nth(2).getByText('unattributed (legacy)')).toBeVisible();
    // The surface column gives way on phones.
    await expect(rows.nth(0).getByText('video', { exact: true })).toHaveCount(1);
    await expect(rows.nth(1).getByText('library', { exact: true })).toHaveCount(1);

    await expect(rows.nth(0).getByRole('link', { name: /^Open chat session .+ in Langfuse/ })).toHaveAttribute('href', VIDEO_SESSION_URL);
    await expect(rows.nth(2).getByRole('link')).toHaveCount(0);

    await shoot(adminPage, 'p2-sessions', testInfo, sessions);
  });
});

test.describe('Video Detail Page — runs', () => {
  test('should show the video attempts newest first, each expandable to its calls', async ({ adminPage }, testInfo) => {
    await adminPage.goto('/videos/abc123');
    const runs = panel(adminPage, 'Pipeline Runs (2)');
    await expect(runs).toBeVisible();

    const rows = runs.locator('tbody > tr');
    await expect(rows.nth(0).getByText('7c1d2e…d4e5')).toBeVisible();
    await expect(rows.nth(1).getByText('b4e8f0…1a2b')).toBeVisible();
    await expect(rows.nth(0).getByRole('link', { name: /^Open trace of run .+ in Langfuse/ })).toHaveAttribute('href', RETRY_RUN.langfuse_url);

    await rows.nth(0).getByRole('button', { name: 'Expand' }).click();
    await expect(runs.getByText('claude-sonnet-4-6')).toBeVisible();
    await expect(runs.getByText('gpt-4o')).toBeVisible();

    await shoot(adminPage, 'p2-video-detail', testInfo);
  });
});
