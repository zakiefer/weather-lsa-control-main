import { spawn } from 'child_process';
import fetch from 'node-fetch';
import { setTimeout as delay } from 'timers/promises';

const STREAMLIT_CMD = ['.venv/bin/python', '-m', 'streamlit', 'run', 'ui/pages/Map.py', '--server.headless', 'true', '--server.port', '8501'];
const HEALTH_URL = 'http://127.0.0.1:8501/';
const PLAYWRIGHT_CMD = ['npx', 'playwright', 'test', '-g', 'Map smoke'];

async function waitForHealth(url, timeoutMs = 60000) {
  const start = Date.now();
  while (Date.now() - start < timeoutMs) {
    try {
      const res = await fetch(url, { method: 'GET' });
      if (res.status === 200) return true;
    } catch {}
    await delay(1000);
  }
  throw new Error('Streamlit app did not become healthy in time');
}

async function main() {
  console.log('Starting Streamlit...');
  const streamlit = spawn(STREAMLIT_CMD[0], STREAMLIT_CMD.slice(1), {
    stdio: 'inherit',
    env: {
      ...process.env,
      STREAMLIT_SERVER_HEADLESS: 'true',
      STREAMLIT_SERVER_ENABLE_CORS: 'false',
      STREAMLIT_SERVER_ENABLE_XSRF_PROTECTION: 'false',
      STREAMLIT_BROWSER_GATHERUSAGESTATS: 'false',
      STREAMLIT_SERVER_ADDRESS: '127.0.0.1',
  // Ensure the app renders without login during E2E and fixtures are enabled
  E2E_AUTH_BYPASS: '1',
  E2E_SPC_FIXTURE: '1',
  E2E_FORCE_SVG: '1',
    },
  });

  let exited = false;
  streamlit.on('exit', () => { exited = true; });

  try {
    await waitForHealth(HEALTH_URL);
  console.log('Streamlit is up. Running Playwright tests...');
  const pw = spawn(PLAYWRIGHT_CMD[0], PLAYWRIGHT_CMD.slice(1), { stdio: 'inherit', env: { ...process.env, E2E_BASE_URL: HEALTH_URL, E2E_AUTH_BYPASS: '1' } });
    await new Promise((resolve, reject) => {
      pw.on('exit', code => (code === 0 ? resolve() : reject(new Error('Playwright failed'))));
    });
  } finally {
    if (!exited) {
      streamlit.kill('SIGINT');
      await delay(3000);
      if (!exited) streamlit.kill('SIGKILL');
    }
    console.log('Playwright report: playwright-report/index.html');
  }
}

main().catch(e => {
  console.error(e);
  process.exit(1);
});
