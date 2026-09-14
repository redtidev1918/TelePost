import { defineConfig } from '@playwright/test';

export default defineConfig({
  testDir: './e2e',
  timeout: 60_000,
  retries: 0,
  webServer: {
    command: 'npx vite --port 3000 --host 127.0.0.1',
    url: 'http://127.0.0.1:3000/app/',
    reuseExistingServer: true,
    timeout: 60_000,
  },
  use: {
    baseURL: 'http://127.0.0.1:3000',
    viewport: { width: 390, height: 844 },
    hasTouch: true,
  },
  projects: [
    { name: 'mobile', use: { viewport: { width: 390, height: 844 } } },
    { name: 'small-mobile', use: { viewport: { width: 320, height: 568 } } },
    { name: 'large-mobile', use: { viewport: { width: 430, height: 932 } } },
    { name: 'desktop', use: { viewport: { width: 1280, height: 800 } } },
  ],
});