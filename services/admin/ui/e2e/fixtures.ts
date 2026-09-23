import { test as base, Page } from '@playwright/test';
import {
  mockUsageStats,
  mockUsageDaily,
  mockUsageByFeature,
  mockUsageByModel,
  mockUsageByService,
  mockUsageRecent,
  mockUsageByVideo,
  mockVideoDetail,
  mockUsageDuplicates,
  mockHealthServices,
  mockHealthOverview,
  mockHealthUptime,
  mockAlertsRecent,
  mockAlertConfig,
  mockUsageByOutputType,
  mockSharesTop,
  mockSharesStats,
  mockTierDistribution,
  mockVideoPurge,
} from './mock-data';

// Re-export mock data for tests that reference it directly
export {
  mockUsageStats,
  mockUsageDaily,
  mockUsageByFeature,
  mockUsageByModel,
  mockUsageByService,
  mockUsageRecent,
  mockUsageByVideo,
  mockVideoDetail,
  mockUsageDuplicates,
  mockHealthServices,
  mockHealthOverview,
  mockHealthUptime,
  mockAlertsRecent,
  mockAlertConfig,
  mockUsageByOutputType,
  mockSharesTop,
  mockSharesStats,
  mockTierDistribution,
} from './mock-data';

// --- Route Interception ---

function fulfill(route: { fulfill: (opts: { status: number; contentType: string; body: string }) => void }, data: unknown) {
  route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(data) });
}

export async function setupAdminApiMocks(page: Page) {
  // Global delete: only the DELETE verb is mocked — the same path is the SPA's
  // own detail page for document navigations.
  await page.route('**/videos/*', (route) => {
    if (route.request().method() === 'DELETE') return fulfill(route, mockVideoPurge);
    return route.continue();
  });
  // Video detail must be registered before by-video to avoid pattern collision
  await page.route('**/usage/video/*', (route) => fulfill(route, mockVideoDetail));
  await page.route('**/usage/by-video*', (route) => fulfill(route, mockUsageByVideo));
  await page.route('**/usage/stats*', (route) => fulfill(route, mockUsageStats));
  await page.route('**/usage/daily*', (route) => fulfill(route, mockUsageDaily));
  await page.route('**/usage/by-feature*', (route) => fulfill(route, mockUsageByFeature));
  await page.route('**/usage/by-model*', (route) => fulfill(route, mockUsageByModel));
  await page.route('**/usage/by-service*', (route) => fulfill(route, mockUsageByService));
  await page.route('**/usage/recent*', (route) => fulfill(route, mockUsageRecent));
  await page.route('**/usage/duplicates*', (route) => fulfill(route, mockUsageDuplicates));
  await page.route('**/usage/by-output-type*', (route) => fulfill(route, mockUsageByOutputType));

  await page.route('**/health/services*', (route) => fulfill(route, mockHealthServices));
  await page.route('**/health/overview*', (route) => fulfill(route, mockHealthOverview));
  await page.route('**/health/uptime*', (route) => fulfill(route, mockHealthUptime));

  await page.route('**/alerts/recent*', (route) => fulfill(route, mockAlertsRecent));
  await page.route('**/alerts/config*', (route) => {
    if (route.request().method() === 'GET') {
      fulfill(route, mockAlertConfig);
    } else {
      fulfill(route, { ok: true });
    }
  });

  await page.route('**/shares/top*', (route) => fulfill(route, mockSharesTop));
  await page.route('**/shares/stats*', (route) => fulfill(route, mockSharesStats));
  await page.route('**/tiers/distribution*', (route) => fulfill(route, mockTierDistribution));
}

// --- Test Fixture ---

export const test = base.extend<{ adminPage: Page }>({
  adminPage: async ({ page }, use) => {
    // Set up API mocks BEFORE navigation
    await setupAdminApiMocks(page);

    // Inject admin API key into localStorage before page scripts run
    await page.addInitScript(() => {
      localStorage.setItem('admin_api_key', 'test-admin-key');
    });

    // Navigate to the app
    await page.goto('/');

    // Wait for the app to render (nav header indicates auth passed)
    await page.waitForSelector('header nav', { timeout: 10_000 });

    await use(page);
  },
});

export { expect } from '@playwright/test';
