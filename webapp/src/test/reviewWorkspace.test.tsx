import { describe, expect, it, vi, beforeEach } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import { AppRoot } from '@telegram-apps/telegram-ui';
import { ReviewWorkspacePage } from '../pages/Review/ReviewWorkspacePage';
import * as reviewsApi from '../api/reviews';

vi.mock('../auth/AuthProvider', () => ({
  AuthProvider: ({ children }: { children: React.ReactNode }) => <>{children}</>,
  useAuth: () => ({
    status: 'authenticated',
    user: { telegram_user_id: 1, username: 'u', roles: ['reviewer'] },
    bootstrap: async () => undefined,
    logout: () => undefined,
    isReviewer: true,
    isAdmin: false,
  }),
}));

beforeEach(() => {
  vi.restoreAllMocks();
});

function renderWorkspace(path: string) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <AppRoot appearance="light">
        <MemoryRouter initialEntries={[path]}>
          <Routes>
            <Route path="/review" element={<ReviewWorkspacePage />} />
            <Route path="/review/history" element={<ReviewWorkspacePage />} />
          </Routes>
        </MemoryRouter>
      </AppRoot>
    </QueryClientProvider>,
  );
}

function item(overrides: Partial<reviewsApi.ReviewSummary> = {}): reviewsApi.ReviewSummary {
  return {
    review_id: 42,
    title: '待审投稿',
    tags: ['#a'],
    media_count: 2,
    document_count: 0,
    spoiler: false,
    source_label: 'PixivFlow',
    created_at: '2026-01-01T00:00:00Z',
    status: 'pending',
    ...overrides,
  };
}

describe('ReviewWorkspacePage（审核 = 一个工作区）', () => {
  it('默认落在「待处理」，并给出待审核数量', async () => {
    vi.spyOn(reviewsApi, 'fetchReviewQueue').mockResolvedValue({
      items: [item(), item({ review_id: 43, title: '第二条' })],
      next_cursor: null,
    });
    renderWorkspace('/review');
    await waitFor(() => expect(screen.getByText('待处理 2 条')).toBeTruthy());
    expect(screen.getByText('待审投稿')).toBeTruthy();
    expect(screen.getByText('第二条')).toBeTruthy();
  });

  it('深链 /review/history 直接落在「历史」', async () => {
    vi.spyOn(reviewsApi, 'fetchReviewHistory').mockResolvedValue({
      items: [item({ status: 'published', title: '已处理的一条' })],
      next_cursor: null,
    });
    renderWorkspace('/review/history');
    await waitFor(() => expect(screen.getByText('已处理的一条')).toBeTruthy());
    expect(screen.getByText('审核历史')).toBeTruthy();
  });

  it('空队列呈现「当前没有待审核内容」，而不是看起来像坏了', async () => {
    vi.spyOn(reviewsApi, 'fetchReviewQueue').mockResolvedValue({ items: [], next_cursor: null });
    vi.spyOn(reviewsApi, 'fetchReviewHistory').mockResolvedValue({ items: [], next_cursor: null });
    renderWorkspace('/review');
    await waitFor(() => expect(screen.getByText('当前没有待审核内容')).toBeTruthy());
    expect(screen.queryByText(/加载失败/)).toBeNull();
  });

  it('审核页面不再暴露刷新频率之类的实现细节', async () => {
    vi.spyOn(reviewsApi, 'fetchReviewQueue').mockResolvedValue({ items: [item()], next_cursor: null });
    renderWorkspace('/review');
    await waitFor(() => expect(screen.getByText('待审投稿')).toBeTruthy());
    expect(document.body.textContent).not.toContain('15 秒');
    expect(document.body.textContent).not.toContain('自动刷新');
  });
});
