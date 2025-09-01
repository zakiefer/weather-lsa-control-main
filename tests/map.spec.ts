import { test, expect, Page } from '@playwright/test';

test.setTimeout(180_000);
const BASE = process.env.E2E_BASE_URL ?? 'http://127.0.0.1:8520';

// Target the Folium map iframe specifically (Streamlit component iframe),
// avoiding the earlier E2E helper iframe on the page. Prefer the component src iframe,
// then fall back to our deterministic id if needed.
const folium = (page: Page) => page.frameLocator('iframe#__map_folium_iframe, iframe[src*="streamlit_folium"], iframe#__map_e2e_iframe').first();

test('Map smoke: overlays and controls (SPC)', async ({ page }) => {
  await page.goto(`${BASE}/?spc=1&spcd=1&spc_fixture=1&svg=1`, { waitUntil: 'domcontentloaded' });
  const host = page.locator('#__e2e_counters_host');
  await expect
    .poll(async () => parseInt((await host.getAttribute('data-spc-added')) || '0', 10), { timeout: 30_000 })
    .toBeGreaterThan(0);
  // Prefer a deterministic marker inside the Folium iframe; fall back to host-level header
  const spcInFrame = folium(page).locator('#__e2e_spc_hdr');
  const spcHost = page.locator('#__e2e_spc_hdr_host');
  await expect
    .poll(async () => (await spcInFrame.count()) > 0 || (await spcHost.count()) > 0 ? 1 : 0, { timeout: 30_000 })
    .toBeGreaterThan(0);
  if (await spcInFrame.count()) {
    await expect(spcInFrame).toHaveText(/SPC Outlook/);
  } else {
    await expect(spcHost).toHaveText(/SPC Outlook/);
  }
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

test('Map timeline scrubber updates timestamp', async ({ page }) => {
  await page.goto(`${BASE}/?svg=1&spc_fixture=1&radar_fixture=1`, { waitUntil: 'domcontentloaded' });

  // Use the deterministic helper iframe controls for timeline
  const helper = page.frameLocator('iframe#__map_e2e_helper, iframe#__map_e2e_iframe').first();
  const lbl = helper.locator('#rv_label').first();
  await expect(lbl).toBeVisible({ timeout: 30_000 });
  const start = await lbl.innerText();

  const nextBtn = helper.locator('#rv_next').first();
  if (await nextBtn.count()) {
    for (let i = 0; i < 6; i++) await nextBtn.click();
  } else {
    await helper.locator('#rv_slider').focus();
    for (let i = 0; i < 6; i++) await page.keyboard.press('ArrowRight');
  }

  await expect
    .poll(async () => ((await lbl.innerText()) !== start ? 1 : 0), { timeout: 30_000 })
    .toBeGreaterThan(0);
});

test('Overlay opacity adjusts layer opacity', async ({ page }) => {
  await page.goto(`${BASE}/?radar=1&svg=1`, { waitUntil: 'domcontentloaded' });

  // Use helper iframe drawer and range input
  const helper = page.frameLocator('iframe#__map_e2e_helper, iframe#__map_e2e_iframe').first();
  await helper.locator('#op_drawer_open').first().click();

  const rv = helper.locator('#op_rv').first();
  await rv.evaluate((el: HTMLInputElement) => {
    el.value = '30';
    el.dispatchEvent(new Event('input', { bubbles: true }));
    el.dispatchEvent(new Event('change', { bubbles: true }));
  });

  // Wait until localStorage shows 30 or the helper label reflects 30%
  await expect
    .poll(async () => {
      const v = await page.evaluate(() => window.localStorage.getItem('rv_opacity'));
      if (v === '30') return 30;
      const txt = await helper.locator('#op_rv_val').first().textContent().catch(() => null);
      return txt?.includes('30%') ? 30 : 0;
    }, { timeout: 30_000 })
    .toBeGreaterThanOrEqual(30);
});
