import { test, expect } from './fixtures';

test.describe('Usage Page', () => {
  test.beforeEach(async ({ adminPage }) => {
    await adminPage.click('nav a:has-text("Usage")');
    await adminPage.waitForURL('**/usage');
  });

  test('cost chart renders', async ({ adminPage }) => {
    await expect(adminPage.getByText('Daily Cost Trend')).toBeVisible();
    const chartContainer = adminPage.locator('.recharts-responsive-container');
    await expect(chartContainer.first()).toBeVisible();
  });

  test('model breakdown pie chart renders', async ({ adminPage }) => {
    await expect(adminPage.getByText('Cost by Model')).toBeVisible();
    // Recharts PieChart renders SVG
    const chartContainer = adminPage.locator('.recharts-responsive-container');
    expect(await chartContainer.count()).toBeGreaterThanOrEqual(2);
  });

  test('feature breakdown bar chart renders', async ({ adminPage }) => {
    await expect(adminPage.getByText('Cost by Feature')).toBeVisible();
  });

  test('recent calls table has correct structure', async ({ adminPage }) => {
    await expect(adminPage.getByText('Recent Calls')).toBeVisible();

    const headers = adminPage.locator('table thead th');
    const headerTexts = await headers.allTextContents();
    expect(headerTexts).toContain('Model');
    expect(headerTexts).toContain('Feature');
    expect(headerTexts).toContain('Cost');
    expect(headerTexts).toContain('Tokens');
    expect(headerTexts).toContain('Duration');
  });

  test('recent calls table shows mock data', async ({ adminPage }) => {
    // Other usage tables (duplicates) name the same models; read the Recent Calls panel only.
    const recent = adminPage
      .locator('[data-slot="panel"]')
      .filter({ has: adminPage.locator('[data-slot="panel-title"]', { hasText: 'Recent Calls' }) })
      .locator('table tbody');

    // Model name from mock data
    await expect(recent.getByText('claude-sonnet-4-6')).toBeVisible();

    // Cost values
    await expect(recent.getByText('$0.0234')).toBeVisible();

    // Tokens (tokens_in + tokens_out = 1500 + 800 = 2300, locale-formatted)
    await expect(recent.getByText('2,300')).toBeVisible();

    // Duration
    await expect(recent.getByText('2100ms')).toBeVisible();
  });

  test('tables are in overflow containers', async ({ adminPage }) => {
    // Wait for the Recent Calls table to render with data
    await expect(adminPage.getByText('Recent Calls')).toBeVisible();
    await expect(adminPage.locator('table').first()).toBeVisible();

    const tables = adminPage.locator('table');
    const count = await tables.count();
    expect(count).toBeGreaterThan(0);

    for (let i = 0; i < count; i++) {
      const parent = tables.nth(i).locator('..');
      const overflowX = await parent.evaluate((el) => getComputedStyle(el).overflowX);
      expect(['auto', 'scroll', 'hidden']).toContain(overflowX);
    }
  });
});
