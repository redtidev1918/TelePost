import { test, expect } from '@playwright/test';
import { openApp } from './support';

test('production CSP permits login, local preview, submission and review navigation', async ({ page }) => {
  await page.addInitScript(() => {
    Object.assign(window, { TelegramWebviewProxy: { postEvent: () => undefined } });
    // Model the native Telegram bridge for a production build (no DEV mock).
    Object.assign(window, { Telegram: { WebApp: { initData:
      'user=%7B%22id%22%3A99999%2C%22first_name%22%3A%22E2E%22%7D&auth_date=1700000000&hash=abc&signature=sig',
      onEvent: () => undefined, offEvent: () => undefined,
      colorScheme: 'dark', platform: 'ios', themeParams: {},
    } } });
    const violations: string[] = [];
    Object.assign(window, { cspViolations: violations });
    document.addEventListener('securitypolicyviolation', (event) => {
      violations.push(`${event.effectiveDirective}: ${event.blockedURI}`);
    });
  });
  const violations: string[] = [];
  await page.exposeFunction('recordCspViolation', (value: string) => violations.push(value));
  await page.addInitScript(() => {
    document.addEventListener('securitypolicyviolation', event => {
      void (window as unknown as { recordCspViolation: (value: string) => Promise<void> })
        .recordCspViolation(`${event.effectiveDirective}: ${event.blockedURI}`);
    });
  });
  const errors: string[] = [];
  page.on('pageerror', error => errors.push(error.message));
  const documentResponse = page.waitForResponse(response => response.request().resourceType() === 'document');
  await openApp(page, '/submit');
  expect((await documentResponse).headers()['content-security-policy']).toContain("script-src 'self'");
  const [chooser] = await Promise.all([
    page.waitForEvent('filechooser'), page.getByTestId('add-files').click(),
  ]);
  await chooser.setFiles({ name: 'photo.png', mimeType: 'image/png', buffer: Buffer.from(
    'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+a6j8AAAAASUVORK5CYII=', 'base64',
  ) });
  await page.getByPlaceholder('标签（必填，可用空格或逗号分隔）').fill('#e2e');
  await page.getByRole('button', { name: /预览投稿/ }).click();
  const image = page.getByTestId('preview-media-0').locator('img');
  await expect(image).toBeVisible();
  await expect.poll(() => image.evaluate(node => (node as HTMLImageElement).naturalWidth)).toBeGreaterThan(0);
  await expect(page.getByTestId('preview-caption')).toContainText('Tags: #e2e');
  await page.getByTestId('preview-back').click();
  await page.getByTestId('submit').click();
  await expect(page.getByText(/投稿已提交/)).toBeVisible();
  await openApp(page, '/review/1');
  await expect(page.getByTestId('edit-before-publish')).toBeVisible();
  expect(await page.evaluate(() => (window as unknown as { cspViolations: string[] }).cspViolations)).toEqual([]);
  expect(errors).toEqual([]);
  expect(violations).toEqual([]);
});
