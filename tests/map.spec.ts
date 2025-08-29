import { test, expect, Page, Frame } from '@playwright/test';

test.setTimeout(180_000);
const BASE = process.env.E2E_BASE_URL ?? 'http://127.0.0.1:8520';

async function loginIfNeeded(page: Page) {
  const onSignIn = await page.getByRole('heading', { name: /sign in/i }).isVisible().catch(() => false);
  if (onSignIn) {
    const user = process.env.E2E_USER ?? 'testuser';
    const pass = process.env.E2E_PASS ?? 'testpass';
    await page.getByLabel(/username/i).fill(user);
    await page.getByLabel(/password/i).fill(pass);
    await page.getByRole('button', { name: /sign in/i }).click();
    await page.waitForLoadState('domcontentloaded');
  }
}

// Best-effort Folium frame discovery (works even if title changes)
async function findFoliumFrame(page: Page): Promise<Frame> {
  const deadline = Date.now() + 30_000;
  while (Date.now() < deadline) {
    try {
      const frames = page.frames();
      for (const f of frames) {
        if (f.url() === 'about:blank') continue;
        const hasLeaflet = await f.locator('#map_div, .leaflet-container').first().isVisible().catch(() => false);
        if (hasLeaflet) return f;
      }
    } catch {}
    await page.waitForTimeout(250);
  }
  throw new Error('Folium/Leaflet frame not found in 30s');
}

test('Map smoke: overlays and controls (SPC)', async ({ page }) => {
  await page.goto(`${BASE}/?spc=1&spcd=1&spc_fixture=1&svg=1`, { waitUntil: 'domcontentloaded' });
  await loginIfNeeded(page);

  // Host-side counter proves SPC overlay was added server-side.
  const host = page.locator('#__e2e_counters_host');
  await expect(host).toHaveAttribute('data-spc-added', /\d+/, { timeout: 30_000 });
  const spcCount = parseInt((await host.getAttribute('data-spc-added')) || '0', 10) || 0;
  expect(spcCount).toBeGreaterThan(0);

  // Lightweight iframe check (don’t rely on network rendering details).
  const folium = await findFoliumFrame(page);
  await expect(folium.locator('#map_div, .leaflet-container').first()).toBeVisible({ timeout: 30_000 });
  await expect(folium.getByText(/SPC Outlook \(Day 1\)/i)).toBeVisible({ timeout: 30_000 });
});

test('Map smoke: radar toggle (host counter)', async ({ page }) => {
  // Turn radar on deterministically
  await page.goto(`${BASE}/?radar=1&radar_fixture=1&svg=1`, { waitUntil: 'domcontentloaded' });
  await loginIfNeeded(page);

  const host = page.locator('#__e2e_counters_host');
  await expect(host).toHaveAttribute('data-radar-added', /\d+/, { timeout: 30_000 });
  const onCount = parseInt((await host.getAttribute('data-radar-added')) || '0', 10) || 0;
  expect(onCount).toBeGreaterThan(0);

  // Flip radar off, then back on (via query params to avoid UI races)
  await page.goto(`${BASE}/?radar=0&radar_fixture=1&svg=1`, { waitUntil: 'domcontentloaded' });
  await expect(host).toHaveAttribute('data-radar-removed', /\d+/, { timeout: 30_000 });
  const offCount = parseInt((await host.getAttribute('data-radar-removed')) || '0', 10) || 0;
  expect(offCount).toBeGreaterThan(0);

  await page.goto(`${BASE}/?radar=1&radar_fixture=1&svg=1`, { waitUntil: 'domcontentloaded' });
  await expect(host).toHaveAttribute('data-radar-added', /\d+/, { timeout: 30_000 });
  const onAgain = parseInt((await host.getAttribute('data-radar-added')) || '0', 10) || 0;
  expect(onAgain).toBeGreaterThan(onCount);

  // Optional light iframe sanity
  const folium = await findFoliumFrame(page);
  await expect(folium.locator('#map_div, .leaflet-container').first()).toBeVisible({ timeout: 30_000 });
});
