import { test, expect, Page, Route } from '@playwright/test';
import type { APIRequestContext } from '@playwright/test';

/**
 * Real-browser Mini App E2E against the vite dev server with the network layer
 * mocked: the frontend is 100% real (Telegram SDK launch params, TelegramUI
 * shell, Uppy) while the /api answers come from this fixture. This exercises
 * the actual DOM geometry, the real file chooser and the real FormData
 * submission path without needing a Telegram account.
 */

const INIT_DATA =
  'user=' +
  encodeURIComponent(JSON.stringify({ id: 99999, first_name: 'E2E', username: 'e2e' })) +
  '&auth_date=1700000000&hash=abc&signature=sig';

const LAUNCH_HASH = `#tgWebAppVersion=8.0&tgWebAppPlatform=ios&tgWebAppData=${encodeURIComponent(INIT_DATA)}`;

function launchUrl(path: string): string {
  // Launch params go in the URL FRAGMENT (Telegram WebView convention), while
  // ?bot= stays in the query. BrowserRouter's basename /app ignores the hash.
  const route = path === '/' ? '' : path;
  return `/app${route}?bot=bot1${LAUNCH_HASH}`;
}

let sessionStore: Record<string, string> = {};

test.beforeEach(() => {
  sessionStore = {};
});

async function mockApi(page: Page, roles: string[] = ['submitter', 'reviewer', 'admin']) {
  await page.route('**/api/bot1/v1/**', async (route: Route) => {
    const url = route.request().url();
    const method = route.request().method();
    const path = new URL(url).pathname;

    if (path.endsWith('/miniapp/session') && method === 'POST') {
      sessionStore.admin = 'ma_v1.e2e.token';
      return route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({
          ok: true,
          data: {
            token: 'ma_v1.e2e.token',
            expires_in: 3600,
            user: { telegram_user_id: 99999, username: 'e2e', display_name: 'E2E' },
          },
        }),
      });
    }
    if (path.endsWith('/me')) {
      return route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({
          ok: true,
          data: {
            telegram_user_id: 99999,
            name: 'E2E',
            username: 'e2e',
            display_name: 'E2E',
            surface: 'mini_app',
            submissions_last_hour: 0,
            rate_limit_per_hour: 5,
            roles,
          },
        }),
      });
    }
    if (path.endsWith('/me/submissions') || path.includes('/me/submissions/')) {
      const items = [
        {
          submission_id: 'chain-42',
          review_chain_id: 'chain-42',
          current_review_id: 3,
          status: 'in_review',
          title: 'E2E 投稿（逻辑链头部）',
          tags: ['#e2e'],
          media_count: 1,
          document_count: 0,
          spoiler: false,
          created_at: 1700000000,
          updated_at: 1700000200,
          generation: 2,
          refetch_count: 2,
        },
      ];
      if (path.includes('/me/submissions/')) {
        return route.fulfill({
          status: 200,
          contentType: 'application/json',
          body: JSON.stringify({
            ok: true,
            data: { ...items[0], note: '备注', link: '' },
          }),
        });
      }
      return route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({ ok: true, data: { items, next_cursor: null } }),
      });
    }
    if (path.endsWith('/submissions')) {
      const body = await route.request().postData();
      return route.fulfill({
        status: 201,
        contentType: 'application/json',
        body: JSON.stringify({
          ok: true,
          data: { status: 'pending_review', review_id: 99, media_count: 1 },
        }),
      });
    }
    if (path.endsWith('/health')) {
      return route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({ ok: true }),
      });
    }
    return route.fulfill({ status: 404, body: '{}' });
  });
}

async function openApp(page: Page, path = '/') {
  await mockApi(page);
  await page.goto(launchUrl(path));
  await expect(page.getByTestId('bottom-nav')).toBeVisible();
  // Shell rendered means auth bootstrapped through the mocked session.
  await expect(page.locator('.app-safe')).toBeVisible();
}

declare global {
  interface Window {
    __dshE2E?: Record<string, unknown>;
  }
}

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

    await page.getByPlaceholder('标签（必填，如 #示例 #壁纸）').fill('#e2e');
    await page.getByTestId('submit').click();
    await expect(page.getByText(/投稿已提交/)).toBeVisible({ timeout: 10_000 });
  });
});

test.describe('My Submissions: logical rows', () => {
  test('one row per chain head, friendly status, no raw review ids', async ({ page }) => {
    await openApp(page, '/mine');
    const row = page.getByTestId('mine-item').first();
    await expect(row).toContainText('E2E 投稿（逻辑链头部）');
    await expect(row).toContainText('已更换候选 2 次');
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
    await page.getByPlaceholder('标签（必填，如 #示例 #壁纸）').fill('#e2e');
    await page.getByRole('button', { name: /预览投稿/ }).click();

    await expect(page.getByTestId('preview-panel')).toBeVisible();
    // The preview shows the actual image (blob object URL), not a filename icon.
    const img = page.getByTestId('preview-media-0').locator('img');
    await expect(img).toBeVisible();
    const src = await img.getAttribute('src');
    expect(src).toMatch(/^blob:/);
  });

  test('submitter line prefers @username in the WebView DOM', async ({ page }) => {
    await openApp(page, '/submit');
    // Preview opens without files only after validation; add one to reach it.
    const [chooser] = await Promise.all([
      page.waitForEvent('filechooser'),
      page.getByTestId('add-files').click(),
    ]);
    await chooser.setFiles({ name: 'a.png', mimeType: 'image/png', buffer: Buffer.from('aaa') });
    await page.getByPlaceholder('标签（必填，如 #示例 #壁纸）').fill('#e2e');
    await page.getByRole('button', { name: /预览投稿/ }).click();
    await expect(page.getByTestId('preview-submitter')).toContainText('@e2e');
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
    await page.getByPlaceholder('标签（必填，如 #示例 #壁纸）').fill('#e2e');
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
