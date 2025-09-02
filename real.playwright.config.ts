import { defineConfig } from '@playwright/test';

export default defineConfig({
  testDir: './real-e2e',
  timeout: 120000,
  expect: { timeout: 60000 },
  reporter: [['list'], ['html', { outputFolder: 'playwright-report-real', open: 'never' }]],
  use: {
    baseURL: 'http://127.0.0.1:8520',
    headless: true,
    viewport: { width: 1280, height: 800 },
  },
  webServer: {
    // Start Map on :8520 via helper script
    command: 'PORT=8520 bash scripts/real_mode_start.sh',
    port: 8520,
    reuseExistingServer: false,
    timeout: 120000,
  },
});
