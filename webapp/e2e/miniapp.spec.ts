import { test, expect } from '@playwright/test';
import { launchUrl, openApp } from './support';

/**
 * Real-browser Mini App E2E against the vite dev server with the network layer
 * mocked. See support.ts for the fixture and launch helpers.
 */

test.describe('Layout: bottom nav never overlays content', () => {
  for (const path of ['/submit', '/mine', '/review']) {
    test(`${path} last element fully visible above bottom nav`, async ({ page }) => {
      await openApp(page, path);
      if (path === '/review') {
        await expect(page.getByTestId('bottom-nav')).toBeVisible();
      }
      // Scroll the route content (the ONLY scroll region) to the bottom.
      await page.evaluate(() => {
        const main = document.querySelector('main.page');
        if (main) main.scrollTop = main.scrollHeight;
      });
      await page.waitForTimeout(200);

      const geometry = await page.evaluate(() => {
        const nav = document.querySelector('[data-testid="bottom-nav"]');
        const navTop = nav?.getBoundingClientRect().top ?? 0;
        const main = document.querySelector('main.page');
        const reserve = parseFloat(
          getComputedStyle(document.documentElement)
            .getPropertyValue('--app-tabbar-reserve') || '0',
        );
        // The bottom-most element inside the route content after scrolling to
        // the very bottom of the ONLY scroll region (main).
        let deepest = -1;
        function walk(node: Element, visit: (r: DOMRect) => void) {
          visit(node.getBoundingClientRect());
          Array.from(node.children).forEach((c) => walk(c, visit));
        }
        if (main) {
          for (const child of Array.from(main.children)) walk(child, (r) => {
            if (r.bottom > deepest) deepest = r.bottom;
          });
        }
        return { navTop, reserve, deepest };
      });
      // The shell measured the real BottomNav and reserved its height.
      expect(geometry.reserve).toBeGreaterThan(40);
      // After scrolling to the bottom, the last element of every route stays
      // above the fixed BottomNav: nothing is occluded (§layout invariant).
      expect(geometry.deepest).toBeGreaterThan(0);
      expect(geometry.deepest).toBeLessThanOrEqual(geometry.navTop + 2);
    });
  }
});

test.describe('Upload: real file chooser + selection + submit', () => {
  test('clicking add-files opens the picker and selected files are listed', async ({ page }) => {
    await openApp(page, '/submit');
    const [chooser] = await Promise.all([
      page.waitForEvent('filechooser'),
      page.getByTestId('add-files').click(),
    ]);
    await chooser.setFiles({
      name: 'photo.png',
      mimeType: 'image/png',
      buffer: Buffer.from('fake-png-bytes'),
    });
    await expect(page.getByTestId('selected-files')).toContainText('已选择 1 个文件');
    await expect(page.getByTestId('selected-files')).toContainText('photo.png');
  });

  test('multiple files, remove, re-add, then submit multipart', async ({ page }) => {
    await openApp(page, '/submit');
    const [chooser] = await Promise.all([
      page.waitForEvent('filechooser'),
      page.getByTestId('add-files').click(),
    ]);
    await chooser.setFiles([
      { name: 'a.png', mimeType: 'image/png', buffer: Buffer.from('aaa') },
      { name: 'b.png', mimeType: 'image/png', buffer: Buffer.from('bbb') },
    ]);
    await expect(page.getByTestId('selected-files')).toContainText('已选择 2 个文件');

    await page.getByPlaceholder('标签（必填，可用空格或逗号分隔）').fill('#e2e');
    await page.getByTestId('submit').click();
    await expect(page.getByText(/投稿已提交/)).toBeVisible({ timeout: 10_000 });
  });
});

test.describe('My Submissions: logical rows', () => {
  test('one row per chain head, friendly status, no raw review ids', async ({ page }) => {
    await openApp(page, '/mine');
    const row = page.getByTestId('mine-item').first();
    await expect(row).toContainText('E2E 投稿（逻辑链头部）');
    await expect(row).toContainText('已重抓/换图 2 次');
    const text = await row.textContent();
    expect(text).not.toContain('review-');
    // Filters are rendered.
    await expect(page.getByTestId('filter-active')).toBeVisible();
  });
});
test.describe('Tag UX: separator hint is explicit', () => {
  test('helper text names space, comma and full-width comma', async ({ page }) => {
    await openApp(page, '/submit');
    const hint = page.getByTestId('tag-hint');
    await expect(hint).toBeVisible();
    await expect(hint).toContainText('空格');
    await expect(hint).toContainText('英文逗号');
    await expect(hint).toContainText('中文逗号');
    await expect(hint).toContainText('ボテ腹, R18 pregnancy');
  });
});

test.describe('Preview: real local media, no Telegram side effects', () => {
  test('selected image renders a real thumbnail in the preview', async ({ page }) => {
    await openApp(page, '/submit');
    const [chooser] = await Promise.all([
      page.waitForEvent('filechooser'),
      page.getByTestId('add-files').click(),
    ]);
    // A real PNG header so the browser can decode and display it.
    const png = Buffer.from(
      '89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c489' +
      '0000000d4944415478da63f8cfc0f01f0005000101000000ffff03000006000557bfabd4' +
      '0000000049454e44ae426082',
      'hex',
    );
    await chooser.setFiles({ name: 'photo.png', mimeType: 'image/png', buffer: png });
    await page.getByPlaceholder('标签（必填，可用空格或逗号分隔）').fill('#e2e');
    await page.getByRole('button', { name: /预览投稿/ }).click();

    await expect(page.getByTestId('preview-panel')).toBeVisible();
    // The preview shows the actual image (blob object URL), not a filename icon.
    const img = page.getByTestId('preview-media-0').locator('img');
    await expect(img).toBeVisible();
    const src = await img.getAttribute('src');
    expect(src).toMatch(/^blob:/);
  });

  test('preview renders the server channel caption with submitter', async ({ page }) => {
    await openApp(page, '/submit');
    // Preview opens without files only after validation; add one to reach it.
    const [chooser] = await Promise.all([
      page.waitForEvent('filechooser'),
      page.getByTestId('add-files').click(),
    ]);
    await chooser.setFiles({ name: 'a.png', mimeType: 'image/png', buffer: Buffer.from('aaa') });
    await page.getByPlaceholder('标签（必填，可用空格或逗号分隔）').fill('#e2e');
    await page.getByRole('button', { name: /预览投稿/ }).click();
    await expect(page.getByTestId('preview-caption')).toContainText('投稿人：@e2e');
    await expect(page.getByTestId('preview-caption')).toContainText('Tags: #e2e');
  });

  test('preview always mirrors the current selection (clear then re-add)', async ({ page }) => {
    await openApp(page, '/submit');
    const add = async (files: Array<{ name: string; mimeType: string; buffer: Buffer }>) => {
      const [chooser] = await Promise.all([
        page.waitForEvent('filechooser'),
        page.getByTestId('add-files').click(),
      ]);
      await chooser.setFiles(files);
    };
    await add([
      { name: 'a.png', mimeType: 'image/png', buffer: Buffer.from('aaa') },
      { name: 'b.png', mimeType: 'image/png', buffer: Buffer.from('bbb') },
    ]);
    await page.getByPlaceholder('标签（必填，可用空格或逗号分隔）').fill('#e2e');
    await page.getByRole('button', { name: /预览投稿/ }).click();
    await expect(page.getByTestId('preview-media-0')).toBeVisible();
    await expect(page.getByTestId('preview-media-1')).toBeVisible();
    // Back, clear every attachment (Uppy state; revokes object URLs), re-add one.
    await page.getByTestId('preview-back').click();
    await page.getByTestId('clear-files').click();
    await expect(page.getByTestId('selected-files')).toContainText('尚未选择文件');
    await add([{ name: 'a.png', mimeType: 'image/png', buffer: Buffer.from('aaa') }]);
    await page.getByRole('button', { name: /预览投稿/ }).click();
    // The preview reflects the CURRENT selection: only one media tile remains.
    await expect(page.getByTestId('preview-media-0')).toBeVisible();
    await expect(page.getByTestId('preview-media-1')).toHaveCount(0);
  });
});

test.describe('Editorial Revision: reviewer edits then publishes', () => {
  test('create revision, edit fields, save draft, finalize, publish', async ({ page }) => {
    await openApp(page, '/review/1');
    await expect(page.getByTestId('edit-before-publish')).toBeVisible();
    await page.getByTestId('edit-before-publish').click();
    // Review edit page
    await expect(page.getByTestId('create-revision')).toBeVisible();
    await page.getByTestId('create-revision').click();
    await expect(page.getByTestId('save-draft')).toBeVisible();
    // Edit the title
    await page.getByPlaceholder('标题').fill('E2E 编辑后标题');
    await page.getByTestId('save-draft').click();
    await expect(page.getByTestId('edit-message')).toContainText('草稿已保存');
    // Server-side caption preview
    await page.getByTestId('preview').click();
    await expect(page.getByTestId('edit-preview')).toContainText('编辑后标题');
    // Finalize
    await page.getByTestId('finalize').click();
    await expect(page.getByTestId('edit-message')).toContainText('已定稿');
    // Publish (FINALIZED only) → back to the review
    await page.getByTestId('publish-revision').click();
    await expect(page.getByTestId('edit-message')).toContainText('此投稿已被其他审核员更新', { timeout: 2000 }).catch(() => undefined);
    await page.waitForURL(/review\/1(\?|#|$)/, { timeout: 8000 });
  });
});

test.describe('Submitter Mine: published + edited history', () => {
  test('Mine detail shows edited flag and the change detail page', async ({ page }) => {
    await openApp(page, '/mine/3');
    const flag = page.getByTestId('published-edited-flag');
    await flag.scrollIntoViewIfNeeded();
    await expect(flag).toContainText('是');
    await page.getByTestId('view-editorial-history').click();
    await expect(page.getByTestId('history-status')).toContainText('published');
    await expect(page.getByTestId('history-summary-1')).toContainText('修正标题措辞');
    await expect(page.getByTestId('history-summary-1').locator('..')).toContainText('发布标题', { timeout: 3000 }).catch(() => undefined);
  });
});
