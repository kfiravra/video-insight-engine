import { test, expect } from './fixtures';

test.describe('Videos Page', () => {
  test('should display video list with metadata', async ({ adminPage }) => {
    await adminPage.getByRole('link', { name: 'Videos' }).click();
    await adminPage.waitForURL('**/videos');

    // Page heading
    await expect(adminPage.getByRole('heading', { name: 'Videos' })).toBeVisible();

    // Video titles from mock data
    await expect(adminPage.getByText('Building a REST API with FastAPI')).toBeVisible();
    await expect(adminPage.getByText('Understanding React Server Components')).toBeVisible();

    // Channel names
    await expect(adminPage.getByText('Tech Channel')).toBeVisible();
    await expect(adminPage.getByText('Frontend Masters')).toBeVisible();

    // Cost values
    await expect(adminPage.getByText('$0.0523')).toBeVisible();
    await expect(adminPage.getByText('$0.0312')).toBeVisible();
  });

  test('should expand video row to show calls', async ({ adminPage }) => {
    await adminPage.getByRole('link', { name: 'Videos' }).click();
    await adminPage.waitForURL('**/videos');

    // Click the expand button for the first video
    const expandBtn = adminPage.locator('[data-testid="expand-abc123"]');
    await expect(expandBtn).toBeVisible();
    await expandBtn.click();

    // Expanded panel should show individual calls
    const panel = adminPage.locator('[data-testid="calls-abc123"]');
    await expect(panel).toBeVisible();

    // Should show call details (model names + feature pills)
    await expect(panel.getByText('claude-sonnet-4-6')).toBeVisible();
    await expect(panel.getByText('gpt-4o')).toBeVisible();
    // Feature pills show "summarize X calls $Y"
    await expect(panel.getByText(/summarize.*calls/)).toBeVisible();
    await expect(panel.getByText(/explain.*calls/)).toBeVisible();
  });

  test('should collapse expanded row on second click', async ({ adminPage }) => {
    await adminPage.getByRole('link', { name: 'Videos' }).click();
    await adminPage.waitForURL('**/videos');

    const expandBtn = adminPage.locator('[data-testid="expand-abc123"]');
    await expandBtn.click();

    const panel = adminPage.locator('[data-testid="calls-abc123"]');
    await expect(panel).toBeVisible();

    // Click again to collapse
    await expandBtn.click();
    await expect(panel).not.toBeVisible();
  });

  test('should navigate to video detail page via link', async ({ adminPage }) => {
    await adminPage.getByRole('link', { name: 'Videos' }).click();
    await adminPage.waitForURL('**/videos');

    await adminPage.getByText('Building a REST API with FastAPI').click();
    await adminPage.waitForURL('**/videos/abc123');

    // Video detail page should show
    await expect(adminPage.getByText('Building a REST API with FastAPI')).toBeVisible();
    await expect(adminPage.getByText('Tech Channel')).toBeVisible();
  });
});

test.describe('Video Detail Page', () => {
  test('should display video metadata and stats', async ({ adminPage }) => {
    await adminPage.goto('/videos/abc123');

    // Back link
    await expect(adminPage.getByRole('link', { name: /Back to Videos/ })).toBeVisible();

    // Video title and channel
    await expect(adminPage.getByText('Building a REST API with FastAPI')).toBeVisible();
    await expect(adminPage.getByText('Tech Channel')).toBeVisible();

    // Stats cards
    await expect(adminPage.getByText('Total Cost')).toBeVisible();
    await expect(adminPage.getByText('$0.0523')).toBeVisible();
    await expect(adminPage.getByText('LLM Calls')).toBeVisible();
    await expect(adminPage.getByText('12', { exact: true })).toBeVisible();

    // Category badge
    await expect(adminPage.getByText('coding')).toBeVisible();

    // Status badge
    await expect(adminPage.getByText('completed')).toBeVisible();
  });

  test('should display feature breakdown', async ({ adminPage }) => {
    await adminPage.goto('/videos/abc123');

    await expect(adminPage.getByText('Cost by Feature')).toBeVisible();
  });

  test('should display individual calls table', async ({ adminPage }) => {
    await adminPage.goto('/videos/abc123');

    await expect(adminPage.getByText('Individual Calls (2)')).toBeVisible();
    await expect(adminPage.getByText('claude-sonnet-4-6')).toBeVisible();
    await expect(adminPage.getByText('gpt-4o')).toBeVisible();
  });

  test('should navigate back to videos list', async ({ adminPage }) => {
    await adminPage.goto('/videos/abc123');

    await adminPage.getByRole('link', { name: /Back to Videos/ }).click();
    await adminPage.waitForURL('**/videos');

    await expect(adminPage.getByRole('heading', { name: 'Videos' })).toBeVisible();
  });
});

test.describe('Video Detail Page — delete everywhere', () => {
  test('should confirm and then show what was removed', async ({ adminPage }) => {
    await adminPage.goto('/videos/abc123');

    await adminPage.getByRole('button', { name: 'Delete everywhere…' }).click();
    await adminPage.getByRole('button', { name: 'Confirm delete' }).click();

    await expect(adminPage.getByTestId('purge-result')).toContainText('userVideos 2');
    await expect(adminPage.getByRole('button', { name: 'Deleted everywhere' })).toBeDisabled();
  });
});
