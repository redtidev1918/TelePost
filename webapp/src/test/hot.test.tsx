import { describe, expect, it, vi, beforeEach } from 'vitest';
import { cleanup, render, screen, fireEvent, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { MemoryRouter } from 'react-router-dom';
import { AppRoot } from '@telegram-apps/telegram-ui';
import { HotPage } from '../pages/Hot/HotPage';
import { PostDetailPage } from '../pages/PostDetail/PostDetailPage';
import { HomePage } from '../pages/Home/HomePage';
import * as postsApi from '../api/posts';
import { ApiError } from '../api/client';
import * as meApi from '../api/me';
import type { PostSummary } from '../api/posts';

vi.mock('../auth/AuthProvider', () => ({
  AuthProvider: ({ children }: { children: React.ReactNode }) => <>{children}</>,
  useAuth: () => ({
    status: 'authenticated',
    user: { telegram_user_id: 1, username: 'u', roles: ['submitter'] },
    bootstrap: async () => undefined,
    logout: () => undefined,
    isReviewer: false,
    isAdmin: false,
  }),
}));

function post(overrides: Partial<PostSummary> = {}): PostSummary {
  return {
    message_id: 11,
    title: '热门帖',
    tags: ['r18'],
    link: 'https://pixiv.example/1',
    publish_time: 1700000000,
    heat_score: 3.2,
    reactions: 5,
    content_type: 'media',
    media_count: 2,
    ...overrides,
  };
}

function renderUi(node: React.ReactNode) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <AppRoot>
        <MemoryRouter initialEntries={['/']}>{node}</MemoryRouter>
      </AppRoot>
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  vi.restoreAllMocks();
  // jsdom lacks blob URL management; the component contract relies on it.
  URL.createObjectURL = vi.fn(() => 'blob:mock');
  URL.revokeObjectURL = vi.fn();
  cleanup();
});

describe('HotPage', () => {
  it('renders items and loads the next cursor page', async () => {
    const spy = vi.spyOn(postsApi, 'fetchHotPosts')
      .mockResolvedValueOnce({ items: [post()], next_cursor: 'cur-1' })
      .mockResolvedValueOnce({ items: [post({ message_id: 12, title: '第二页' })], next_cursor: null });
    renderUi(<HotPage scope="all" />);
    expect(await screen.findByTestId('post-card')).toBeTruthy();
    expect(screen.getByText('热门帖')).toBeTruthy();
    fireEvent.click(screen.getByTestId('load-more'));
    expect(await screen.findByText('第二页')).toBeTruthy();
    expect(spy).toHaveBeenNthCalledWith(2, 'all', 'cur-1');
  });

  it('shows the week scope header', async () => {
    vi.spyOn(postsApi, 'fetchHotPosts').mockResolvedValue({ items: [], next_cursor: null });
    renderUi(<HotPage scope="week" />);
    expect(await screen.findByText('本周热门')).toBeTruthy();
    expect(await screen.findByText('还没有热门内容。')).toBeTruthy();
  });
});

describe('PostDetailPage', () => {
  it('renders the safe public DTO without internal identity', async () => {
    vi.spyOn(postsApi, 'fetchPost').mockResolvedValue({
      ...post(),
      note: '简介内容',
    });
    vi.spyOn(postsApi, 'fetchPostMedia').mockResolvedValue(new Blob(['x']));
    renderUi(<PostDetailPage />);
    expect(await screen.findByText('热门帖')).toBeTruthy();
    expect(await screen.findByText('简介内容')).toBeTruthy();
    expect(await screen.findByTestId('post-media')).toBeTruthy();
    const html = document.body.innerHTML;
    expect(html).not.toContain('file_ids');
    expect(html).not.toContain('username');
  });

  it('renders without media when media_count is 0', async () => {
    vi.spyOn(postsApi, 'fetchPost').mockResolvedValue({
      ...post({ media_count: 0 }),
      note: '',
    });
    const mediaSpy = vi.spyOn(postsApi, 'fetchPostMedia');
    renderUi(<PostDetailPage />);
    expect(await screen.findByText('热门帖')).toBeTruthy();
    await waitFor(() => expect(mediaSpy).not.toHaveBeenCalled());
  });
});

describe('HomePage content entry', () => {
  it('renders hot previews and keeps the submit entrypoints', async () => {
    vi.spyOn(postsApi, 'fetchHotPosts').mockResolvedValue({
      items: [post()],
      next_cursor: null,
    });
    vi.spyOn(meApi, 'fetchMe').mockResolvedValue({
      telegram_user_id: 1,
      name: '用户 1',
      surface: 'mini_app',
      submissions_last_hour: 0,
      rate_limit_per_hour: 10,
      roles: ['submitter'],
    });
    renderUi(<HomePage />);
    expect(await screen.findByTestId('post-card')).toBeTruthy();
    expect(screen.getByText('投稿')).toBeTruthy();
  });

  it('silently hides hot sections when the content API is disabled (404)', async () => {
    vi.spyOn(postsApi, 'fetchHotPosts').mockRejectedValue(
      new ApiError(404, 'not_found', '内容浏览未开启'),
    );
    vi.spyOn(meApi, 'fetchMe').mockResolvedValue({
      telegram_user_id: 1,
      name: '用户 1',
      surface: 'mini_app',
      submissions_last_hour: 0,
      rate_limit_per_hour: 10,
      roles: ['submitter'],
    });
    renderUi(<HomePage />);
    await waitFor(() => {
      expect(screen.queryByTestId('post-card')).toBeNull();
    });
    // Submission entrypoints must survive the optional-feature gate.
    expect(screen.getByText('投稿')).toBeTruthy();
    expect(screen.getByText('我的投稿')).toBeTruthy();
  });
});
