import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';

// Mini App is served from the same domain as the TelePost API (§68), e.g.
// https://telepost.example/app/ with the API at /api/v1/. In dev we proxy
// /api to the local TelePost API server.
export default defineConfig({
  plugins: [react()],
  base: '/app/',
  server: {
    port: 3000,
    proxy: {
      '/api': {
        target: 'http://127.0.0.1:8080',
        changeOrigin: true,
      },
    },
  },
  build: {
    outDir: 'dist',
    sourcemap: false,
    chunkSizeWarningLimit: 900,
  },
  test: {
    environment: 'jsdom',
    globals: true,
    setupFiles: ['src/test/setup.ts'],
    // e2e/ is Playwright territory; vitest must never collect it.
    exclude: ['e2e/**', 'node_modules/**', 'dist/**', 'test-results/**'],
  },
});