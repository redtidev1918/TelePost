import { defineConfig } from '@playwright/test';

export default defineConfig({
  testDir: './e2e',
  testMatch: 'security.spec.ts',
  timeout: 60_000,
  webServer: {
    command: `${process.env.CSP_PYTHON || 'python'} e2e/csp_server.py`,
    url: 'http://127.0.0.1:3001/app/',
    reuseExistingServer: false,
  },
  use: { baseURL: 'http://127.0.0.1:3001', viewport: { width: 390, height: 844 } },
});
