import React from 'react';
import ReactDOM from 'react-dom/client';
import { isTMA, mockTelegramEnv } from '@telegram-apps/sdk';
import { AppRoot } from '@telegram-apps/telegram-ui';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { BrowserRouter } from 'react-router-dom';
import '@telegram-apps/telegram-ui/dist/styles.css';
import './index.css';
import { App } from './app/App';

// The SDK reads Telegram launch params; mock only in local development.
if (!isTMA() && import.meta.env.DEV) {
  mockTelegramEnv({
    launchParams: {
      tgWebAppData: new URLSearchParams([
        ['user', JSON.stringify({ id: 12345, first_name: 'Dev', username: 'devuser' })],
        ['auth_date', String(Math.floor(Date.now() / 1000))],
        ['hash', 'dev'],
        ['signature', 'dev'],
      ]),
      tgWebAppVersion: '8.0',
      tgWebAppPlatform: 'ios',
      tgWebAppThemeParams: ({
        accent_text_color: '#6ab2f2' as `#${string}`,
        bg_color: '#17212b',
        button_color: '#5288c1',
        button_text_color: '#ffffff',
        destructive_text_color: '#ec3942',
        header_bg_color: '#17212b',
        hint_color: '#708499',
        link_color: '#6ab3f3',
        secondary_bg_color: '#232e3c',
        section_bg_color: '#17212b',
        section_header_text_color: '#708499',
        subtitle_text_color: '#708499',
        text_color: '#f5f5f5',
      } as never),
    },
  });
}

const queryClient = new QueryClient({
  defaultOptions: { queries: { staleTime: 10_000, retry: 1 } },
});

ReactDOM.createRoot(document.getElementById('root')!).render(
  <React.StrictMode>
    <QueryClientProvider client={queryClient}>
      <AppRoot appearance="light">
        <BrowserRouter basename="/app">
          <App />
        </BrowserRouter>
      </AppRoot>
    </QueryClientProvider>
  </React.StrictMode>,
);
