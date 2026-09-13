/**
 * Minimal typing for the Telegram WebApp JS bridge the Mini App runs inside.
 * The real object is injected by Telegram; this is only the surface we use.
 */
interface TelegramHapticFeedback {
  notificationOccurred(
    type: 'error' | 'success' | 'warning',
  ): void;
  impactOccurred(style: 'light' | 'medium' | 'heavy' | 'rigid' | 'soft'): void;
}

interface TelegramMainButton {
  setText(text: string): void;
  show(): void;
  hide(): void;
  setParams(params: { text?: string; is_active?: boolean; is_visible?: boolean }): void;
  onClick(cb: () => void): void;
  offClick(cb: () => void): void;
}

interface TelegramWebApp {
  initData: string;
  initDataUnsafe: Record<string, unknown>;
  ready(): void;
  expand(): void;
  close(): void;
  themeParams: Record<string, string>;
  BackButton: {
    show(): void;
    hide(): void;
    onClick(cb: () => void): this;
    offClick(cb: () => void): this;
  };
  MainButton?: TelegramMainButton;
  HapticFeedback?: TelegramHapticFeedback;
  setHeaderColor?(color: string): void;
  setBackgroundColor?(color: string): void;
  version: string;
  platform: string;
  colorScheme: string;
}

declare global {
  interface Window {
    Telegram?: {
      WebApp?: Partial<TelegramWebApp>;
    };
  }
}

export {};
