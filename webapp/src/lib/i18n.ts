import i18n from 'i18next';
import { initReactI18next } from 'react-i18next';
import en from '../locales/en.json';
import zh from '../locales/zh.json';

const launchLanguage = new URLSearchParams(window.location.search).get('lang');
void i18n.use(initReactI18next).init({
  resources: { en: { translation: en }, zh: { translation: zh } },
  lng: launchLanguage === 'en' ? 'en' : 'zh',
  fallbackLng: 'zh',
  supportedLngs: ['zh', 'en'],
  keySeparator: false,
  nsSeparator: false,
  initAsync: false,
  interpolation: { escapeValue: false }, // React escapes text at the DOM boundary.
});

export const tr = (message: string, values?: Record<string, unknown>): string =>
  i18n.t(message, values ?? {});

export async function setBotLanguage(language: unknown): Promise<void> {
  await i18n.changeLanguage(language === 'en' ? 'en' : 'zh');
  document.documentElement.lang = i18n.language;
}

export default i18n;
