import { test, expect, Page } from '@playwright/test';

test.setTimeout(180_000);
const BASE = process.env.E2E_BASE_URL ?? 'http://127.0.0.1:8520';

const folium = (page: Page) => page.frameLocator('iframe').first();

test('Map smoke: overlays and controls (SPC)', async ({ page }) => {
  await page.goto(`${BASE}/?spc=1&spcd=1&spc_fixture=1&svg=1`, { waitUntil: 'domcontentloaded' });
  const host = page.locator('#__e2e_counters_host');
  await expect
    .poll(async () => parseInt((await host.getAttribute('data-spc-added')) || '0', 10), { timeout: 30_000 })
    .toBeGreaterThan(0);
  await expect(folium(page).getByText(/SPC Outlook \(Day 1\)/i)).toBeVisible({ timeout: 30_000 });
});

test('Map smoke: radar toggle (host counter)', async ({ page }) => {
  const host = page.locator('#__e2e_counters_host');

  await page.goto(`${BASE}/?radar=1&radar_fixture=1&svg=1`, { waitUntil: 'domcontentloaded' });
  await expect
    .poll(async () => parseInt((await host.getAttribute('data-radar-added')) || '0', 10), { timeout: 30_000 })
    .toBeGreaterThanOrEqual(1);

  const startRemoved = parseInt((await host.getAttribute('data-radar-removed')) || '0', 10);
  await page.goto(`${BASE}/?radar=0&radar_fixture=1&svg=1`, { waitUntil: 'domcontentloaded' });
  await expect
    .poll(async () => parseInt((await host.getAttribute('data-radar-removed')) || '0', 10), { timeout: 30_000 })
    .toBeGreaterThan(startRemoved);

  const startAdded2 = parseInt((await host.getAttribute('data-radar-added')) || '0', 10);
  await page.goto(`${BASE}/?radar=1&radar_fixture=1&svg=1`, { waitUntil: 'domcontentloaded' });
  await expect
    .poll(async () => parseInt((await host.getAttribute('data-radar-added')) || '0', 10), { timeout: 30_000 })
    .toBeGreaterThan(startAdded2);
});
