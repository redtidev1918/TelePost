import { test, expect } from '@playwright/test';
import { openApp } from './support';

/**
 * Visual baseline: these tests intentionally do not replace semantic E2E.
 * They catch layout drift that functional assertions cannot see — squeezed
 * actions, broken cards, missing spacing and overflow on long content.
 * Start with one representative mobile viewport; add more only when stable.
 */


async function stableScreenshot(page: import('@playwright/test').Page, name: string) {
  await page.addStyleTag({
    content: '*, *::before, *::after { animation: none !important; transition: none !important; caret-color: transparent !important; }',
  });
  await expect(page.locator('.app-safe')).toBeVisible();
  await expect(page.getByTestId('bottom-nav')).toBeVisible();
  await expect(page).toHaveScreenshot(name, {
    fullPage: true,
    animations: 'disabled',
    caret: 'hide',
  });
}

for (const path of ['/', '/submit', '/mine', '/review', '/admin']) {
  test(`visual baseline: ${path}`, async ({ page }, testInfo) => {
    test.skip(testInfo.project.name !== 'mobile', 'visual baseline runs on the primary mobile viewport only');
    await openApp(page, path);
    await stableScreenshot(page, `page${path.replace('/', '-') || '-home'}.png`);
  });
}