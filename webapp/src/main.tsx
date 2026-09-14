import React from 'react';
import ReactDOM from 'react-dom/client';
import { init, isTMA, miniApp, mockTelegramEnv, viewport } from '@telegram-apps/sdk';
import { AppRoot } from '@telegram-apps/telegram-ui';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { BrowserRouter } from 'react-router-dom';
import '@telegram-apps/telegram-ui/dist/styles.css';
import './index.css';
import { App } from './app/App';

// Telegram SDK must initialise synchronously. Dev/tests only: production runs
// inside the real Telegram WebView which injects window.Telegram.WebApp.
// The mock must satisfy the CURRENT @telegram-apps/sdk launch-params schema:
// initData requires auth_date + hash + signature, and user needs first_name.
if (!isTMA() && import.meta.env.DEV) {
  mockTelegramEnv({
    launchParams: {
      tgWebAppData: new URLSearchParams([
        ['user', JSON.stringify({ id: 12345, first_name: 'Dev', username: 'devuser' })],
        ['auth_date', String(Math.floor(Date.now() / 1000))],
        ['hash', 'dev'],
        ['signature', 'dev-signature'],
      ]),
      tgWebAppVersion: '8.0',
      tgWebAppPlatform: 'ios',
      tgWebAppThemeParams: ({
        accent_text_color: '#6ab2f2' as `#${string}`,
        bg_color: '#17212b' as `#${string}`,
        button_color: '#5288c1' as `#${string}`,
        button_text_color: '#ffffff' as `#${string}`,
        destructive_text_color: '#ec3942' as `#${string}`,
        header_bg_color: '#17212b' as `#${string}`,
        hint_color: '#708499' as `#${string}`,
        link_color: '#6ab3f3' as `#${string}`,
        secondary_bg_color: '#232e3c' as `#${string}`,
        section_bg_color: '#17212b' as `#${string}`,
        section_header_text_color: '#708499' as `#${string}`,
        subtitle_text_color: '#708499' as `#${string}`,
        text_color: '#f5f5f5' as `#${string}`,
      } as never),
    },
  });
}

// The SDK owns Telegram viewport events and CSS bindings, including fullscreen
// safe areas. Mounting must not block authentication on older Telegram clients.
if (isTMA()) {
  init();
  miniApp.ready.ifAvailable();
  if (viewport.mount.isAvailable()) {
    void viewport.mount({ timeout: 3000 }).then(() => {
      viewport.bindCssVars();
    }).catch(() => {
      // CSS env()/dvh remain the browser fallback when Telegram sends no viewport.
    });
  }
}

const queryClient = new QueryClient({
  defaultOptions: { queries: { staleTime: 10_000, retry: 1 } },
});

ReactDOM.createRoot(document.getElementById('root')!).render(
  <React.StrictMode>
    <QueryClientProvider client={queryClient}>
      <AppRoot className="telegram-root">
        <BrowserRouter basename="/app">
          <App />
        </BrowserRouter>
      </AppRoot>
    </QueryClientProvider>
  </React.StrictMode>,
);
