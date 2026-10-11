import { expect, Page, Route } from '@playwright/test';

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

/** 1x1 PNG: enough for the browser to decode a real image for media previews. */
const PNG_1X1 = Buffer.from(
  '89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c489' +
    '0000000d4944415478da63f8cfc0f01f0005000101000000ffff03000006000557bfabd4' +
    '0000000049454e44ae426082',
  'hex',
);

function launchUrl(path: string): string {
  // Launch params go in the URL FRAGMENT (Telegram WebView convention), while
  // ?bot= stays in the query. BrowserRouter's basename /app ignores the hash.
  const route = path === '/' ? '/' : path;
  return `/app${route}?bot=bot1${LAUNCH_HASH}`;
}

async function mockApi(page: Page, roles: string[] = ['submitter', 'reviewer', 'admin'], language = 'zh') {
  await page.route('**/api/bot1/v1/**', async (route: Route) => {
    const url = route.request().url();
    const method = route.request().method();
    const path = new URL(url).pathname;

    if (path.endsWith('/miniapp/session') && method === 'POST') {
      return route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({
          ok: true,
          data: {
            token: 'ma_v1.e2e.token',
            expires_in: 3600,
            bot_language: language,
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
    if (/^\/api\/bot1\/v1\/reviews\/\d+$/.test(path) && method === 'GET') {
      return route.fulfill({
        status: 200, contentType: 'application/json',
        body: JSON.stringify({
          ok: true,
          data: {
            id: 1, status: 'pending', title: 'E2E 审核稿', note: '简介', tags: ['#e2e'],
            link: '', anonymous: false, spoiler: false, submitter_name: 'e2e',
            submitter_id: 99999, submitter_user_id: 99999, source_label: '投稿',
            source_ref: '', scheduled_at: '', source: 'api', target_id: 'bot1-illust-botefuku',
            media: [{ index: 0, kind: 'photo', file_id: 'A', filename: 'a.png' }],
            created_at: '2026-09-15T00:00:00Z', updated_at: '2026-09-15T00:00:00Z',
            error: '',
          },
        }),
      });
    }
    if (/^\/api\/bot1\/v1\/reviews$/.test(path) && method === 'GET') {
      return route.fulfill({ status: 200, contentType: 'application/json',
        body: JSON.stringify({ ok: true, data: { items: [{ review_id: 1, title: 'E2E 审核稿', tags: ['#e2e'], media_count: 1, document_count: 0, spoiler: false, source_label: '投稿', created_at: '2026-09-15T00:00:00Z', status: 'pending' }], next_cursor: null } }) });
    }
    if (path.includes('/editorial-history') && method === 'GET') {
      return route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ ok: true, data: { review_id: 3, status: 'published', edited_before_publication: true, published_message_id: 888, revisions: [{ revision_number: 1, status: 'published', summary: '修正标题措辞', change_set: { title: { before: '原稿标题', after: '发布标题' } }, edited_snapshot: { title: '发布标题', tags: '#e2e', note: '', link: '', spoiler: false, media_order: [0], removed: [] }, published_snapshot: { title: '发布标题', tags: '#e2e', note: '', link: '', spoiler: false, media_order: [0], removed: [] }, finalized_at: 1700001000, published_at: 1700002000, published_message_id: 888, editor_display: '频道管理员' }] } }) });
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
    if (path.endsWith('/submissions/preview') && method === 'POST') {
      return route.fulfill({ status: 200, contentType: 'application/json',
        body: JSON.stringify({ ok: true, data: { caption: '🔖 标题： \n【E2E】\n🏷 Tags: #e2e\n\n投稿人：<a href="tg://user?id=99999">@e2e</a>' } }) });
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
    if (path.includes('/editorial-revisions')) {
      const store: any = (globalThis as any).__editorialStore ?? { revisions: [], nextId: 1 };
      (globalThis as any).__editorialStore = store;
      const base = path.replace(/^\/api\/bot1\/v1\/reviews\/\d+\//, '/');
      if (base.endsWith('/editorial-revisions') && method === 'POST') {
        const rev = {
          id: store.nextId++,
          review_id: 1,
          revision_number: store.revisions.length + 1,
          status: 'draft',
          severity: 'minor',
          summary: '',
          change_set: {},
          base_snapshot: { title: '', note: '', tags: '', link: '', spoiler: false, media_order: [0], removed: [] },
          edited_snapshot: { title: '', note: '', tags: '', link: '', spoiler: false, media_order: [0], removed: [] },
          version: 1,
          editor_display: '',
          created_at: 1700000000, updated_at: 1700000000,
          finalized_at: null, published_at: null, published_message_id: null,
          published_snapshot: {},
        };
        store.revisions.push(rev);
        // auto-load this review's fields as the base
        rev.edited_snapshot.title = 'E2E 标题';
        rev.edited_snapshot.tags = '#e2e';
        return route.fulfill({ status: 201, contentType: 'application/json', body: JSON.stringify({ ok: true, data: rev }) });
      }
      if (base.endsWith('/editorial-revisions') && method === 'GET') {
        return route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ ok: true, data: { revisions: store.revisions } }) });
      }
      if (method === 'PATCH') {
        const id = Number(path.split('/').pop());
        const rev = store.revisions.find((r: any) => r.id === id);
        const patch = JSON.parse((route.request().postData() as string) || '{}');
        rev.edited_snapshot = { ...rev.edited_snapshot, title: patch.title ?? rev.edited_snapshot.title, tags: patch.tags ?? rev.edited_snapshot.tags, note: patch.note ?? rev.edited_snapshot.note };
        rev.change_set = patch.title && patch.title !== 'E2E 标题' ? { title: { before: 'E2E 标题', after: patch.title } } : {};
        rev.summary = rev.change_set.title ? '修正标题措辞' : '';
        rev.version += 1;
        return route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ ok: true, data: rev }) });
      }
      if (path.endsWith('/finalize') && method === 'POST') {
        const id = Number(path.split('/').slice(-2)[0]);
        const rev = store.revisions.find((r: any) => r.id === id);
        rev.status = 'finalized';
        rev.version += 1;
        return route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ ok: true, data: rev }) });
      }
      if (path.endsWith('/preview') && method === 'POST') {
        return route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ ok: true, data: { caption: '编辑后标题\n🏷 #e2e' } }) });
      }
      return route.fulfill({ status: 404, body: '{}' });
    }
    if (path.endsWith('/publish') && method === 'POST') {
      return route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ ok: true, data: { review_id: 1, status: 'published', reused: false, message_id: 888, link: 'https://t.me/c/1/888' } }) });
    }
    if (path.includes('/posts/hot') && method === 'GET') {
      return route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({
          ok: true,
          data: {
            items: [
              {
                message_id: 101,
                title: 'E2E 热门内容：一个刻意写得比较长的标题',
                tags: ['#e2e', '#r18'],
                link: '',
                publish_time: 1700000000,
                heat_score: 4.2,
                reactions: 7,
                content_type: 'media',
                media_count: 2,
              },
              {
                message_id: 102,
                title: '第二条热门内容',
                tags: ['#second'],
                link: '',
                publish_time: 1700000100,
                heat_score: 1.5,
                reactions: 0,
                content_type: 'media',
                media_count: 1,
              },
              { message_id: 103, title: '无媒体的条目', tags: [], link: '', publish_time: 1700000200, heat_score: 0.4, reactions: 3, content_type: 'text', media_count: 0 },
            ],
            next_cursor: null,
          },
        }),
      });
    }
    if (/^\/api\/bot1\/v1\/posts\/\d+$/.test(path) && method === 'GET') {
      return route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({
          ok: true,
          data: {
            message_id: 101,
            title: 'E2E 热门内容：一个刻意写得比较长的标题',
            tags: ['#e2e', '#r18'],
            link: 'https://example.com/source',
            publish_time: 1700000000,
            heat_score: 4.2,
            reactions: 7,
            content_type: 'media',
            media_count: 2,
            note: '这是一条用于 E2E 的简介。',
          },
        }),
      });
    }
    if (/^\/api\/bot1\/v1\/posts\/\d+\/media\/\d+/.test(path) && method === 'GET') {
      return route.fulfill({ status: 200, contentType: 'image/png', body: PNG_1X1 });
    }
    if (path.endsWith('/admin/status') && method === 'GET') {
      return route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({
          ok: true,
          data: {
            version: { version: '2.79.0', commit: 'abcdef1234567890' },
            queue: { pending: 1, staging: 0, failed: 0, superseded: 0, published: 40, rejected: 5 },
            refetch: { active: 0, recent_failures: [] },
            submissions_24h: 12,
            blacklist_size: 1,
            policy: {
              api_review_required: true,
              miniapp_review_required: true,
              chat_review_required: false,
              show_submitter: true,
              overrides: [],
              review_chat_configured: true,
              channel_configured: true,
            },
            restart_managed: true,
          },
        }),
      });
    }
    if (path.endsWith('/admin/roles') && method === 'GET') {
      return route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({
          ok: true,
          data: [{ telegram_user_id: 42, role: 'reviewer', created_by: 'telegram_user:1', created_at: '2026-09-19T00:00:00Z' }],
        }),
      });
    }
    if (path.endsWith('/admin/blacklist') && method === 'GET') {
      return route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({ ok: true, data: [{ user_id: 7, reason: '刷屏', added_at: '2026-09-19T00:00:00Z' }] }),
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

async function openApp(page: Page, path = '/', roles?: string[], language = 'zh') {
  await mockApi(page, roles, language);
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

export { launchUrl, mockApi, openApp };
