import { defineConfig, devices } from '@playwright/test';

export default defineConfig({
  testDir: 'tests',
  timeout: 60_000,
  expect: { timeout: 30_000 },
  reporter: [['list'], ['html', { outputFolder: 'playwright-report' }]],
  use: {
    headless: true,
    baseURL: 'http://127.0.0.1:8520',
    viewport: { width: 1440, height: 900 }
  },
  webServer: {
    // Source .venv if present; otherwise run in system env.
    command: 'bash -lc "[ -f .venv/bin/activate ] && source .venv/bin/activate; E2E_AUTH_BYPASS=1 E2E_SPC_FIXTURE=1 E2E_FORCE_SVG=1 streamlit run ui/pages/Map.py --server.headless true --server.port 8520"',
    url: 'http://127.0.0.1:8520',
    reuseExistingServer: true,
    timeout: 120000
  },
  projects: [{ name: 'chromium', use: { ...devices['Desktop Chrome'] } }],
  // (Optional) Add one retry to de-flake CI a bit:
  // retries: 1
});
