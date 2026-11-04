import { expect, test } from '@playwright/test';
import { openApp } from './support';

test('published review refreshes in place and offers a visible channel entry without a source', async ({ page }, testInfo) => {
  await openApp(page, '/review/1');
  await expect(page.getByTestId('approve')).toBeVisible();
  await page.route('**/api/bot1/v1/reviews/1', async (route) => {
    await route.fulfill({ json: { ok: true, data: {
      id: 1, status: 'published', title: 'Example publication', note: '', tags: [],
      link: '', channel_url: 'https://t.me/example_channel/81', media: [],
      source: 'miniapp', spoiler: false, created_at: '', updated_at: '', error: '',
    } } });
  });
  const channel = page.getByTestId('review-channel');
  await expect(channel).toBeVisible({ timeout: 8000 });
  await expect(page.getByTestId('approve')).toHaveCount(0);
  const box = await channel.boundingBox();
  const nav = await page.getByTestId('bottom-nav').boundingBox();
  expect(box!.y + box!.height).toBeLessThan(nav!.y);
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
  await page.screenshot({ path: testInfo.outputPath('published-review.png'), fullPage: true });

  await page.getByTestId('bottom-nav').getByRole('button', { name: '审核' }).click();
  const more = page.getByTestId('review-more');
  await expect(more).toBeVisible();
  const moreBox = await more.boundingBox();
  expect(moreBox!.height).toBeGreaterThanOrEqual(36);
  await page.screenshot({ path: testInfo.outputPath('review-more.png'), fullPage: true });
  await more.click();
  await expect(page).toHaveURL(/\/more/);
});
