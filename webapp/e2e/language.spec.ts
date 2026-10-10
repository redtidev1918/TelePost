import { expect, test } from '@playwright/test';
import { openApp } from './support';

test('English submission preserves content and fits the viewport', async ({ page }, testInfo) => {
  await openApp(page, '/submit', ['submitter'], 'en');
  await expect(page.getByTestId('bottom-nav')).toContainText('Home');
  expect(await page.getByTestId('nav-home').evaluate((button) => {
    const label = button.lastElementChild as HTMLElement;
    return label.scrollWidth <= label.clientWidth;
  })).toBe(true);
  await expect(page.getByTestId('tag-hint')).toContainText('Separate tags with spaces');
  await page.getByTestId('file-input').setInputFiles({
    name: '中文图片.png', mimeType: 'image/png', buffer: Buffer.from('fake-image'),
  });
  await expect(page.getByTestId('selected-files')).toContainText('1 files selected');
  await page.getByPlaceholder('Tags (required; spaces or commas)').fill('#中文标签');
  await page.getByPlaceholder('Title (optional)').fill('中文标题');
  await page.getByTestId('submit').scrollIntoViewIfNeeded();
  const geometry = await page.getByTestId('submit').boundingBox();
  const nav = await page.getByTestId('bottom-nav').boundingBox();
  expect(geometry!.y + geometry!.height).toBeLessThanOrEqual(nav!.y + 2);
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
  await page.screenshot({ path: testInfo.outputPath('english-submit.png'), fullPage: true });
  await page.getByTestId('submit').click();
  await expect(page.getByText('Submission sent ✅')).toBeVisible();
});

test('English review actions keep the original submitted title', async ({ page }, testInfo) => {
  await openApp(page, '/review/1', undefined, 'en');
  await expect(page.getByTestId('bottom-nav')).toContainText('Review');
  await expect(page.getByRole('button', { name: 'Approve and publish' })).toBeVisible();
  await expect(page.getByText('E2E 审核稿')).toBeVisible();
  await expect(page.getByRole('button', { name: 'Edit and publish' })).toBeVisible();
  await page.getByRole('button', { name: 'Approve and publish' }).scrollIntoViewIfNeeded();
  await page.screenshot({ path: testInfo.outputPath('english-review.png'), fullPage: true });
});
