import '@testing-library/jest-dom/vitest';
import { afterEach, vi } from 'vitest';
import { cleanup } from '@testing-library/react';

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

// Stub the Telegram bridge for tests (dev mock covers UI preload).
Object.defineProperty(window, 'Telegram', {
  value: {
    WebApp: {
      initData: '',
      initDataUnsafe: {},
      ready: vi.fn(),
      expand: vi.fn(),
      close: vi.fn(),
      themeParams: {},
      colorScheme: 'light',
      platform: 'test',
      version: '7.0',
      onEvent: vi.fn(),
      offEvent: vi.fn(),
      BackButton: { show: vi.fn(), hide: vi.fn(), onClick: vi.fn().mockReturnThis(), offClick: vi.fn().mockReturnThis() },
      HapticFeedback: { notificationOccurred: vi.fn(), impactOccurred: vi.fn() },
    },
  },
  configurable: true,
  writable: true,
});
