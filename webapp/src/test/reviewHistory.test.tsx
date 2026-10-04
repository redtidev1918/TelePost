import { describe, expect, it, vi, beforeEach } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import { AppRoot } from '@telegram-apps/telegram-ui';
import { ReviewHistoryPage } from '../pages/ReviewHistory/ReviewHistoryPage';
import * as reviewsApi from '../api/reviews';

beforeEach(() => {
  vi.restoreAllMocks();
});

function renderHistory() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(
    <QueryClientProvider client={qc}>
      <AppRoot appearance="light">
        <MemoryRouter initialEntries={['/review/history']}>
          <Routes>
            <Route path="/review/history" element={<ReviewHistoryPage />} />
          </Routes>
        </MemoryRouter>
      </AppRoot>
    </QueryClientProvider>,
  );
}

function item(overrides: Partial<reviewsApi.ReviewSummary> = {}): reviewsApi.ReviewSummary {
  return {
    review_id: 42,
    title: '已审投稿',
    tags: ['#a'],
    media_count: 2,
    document_count: 0,
    spoiler: false,
    source_label: 'Telegram',
    created_at: '2026-01-01T00:00:00Z',
    status: 'published',
    ...overrides,
  };
}

describe('ReviewHistoryPage (§history)', () => {
  it('渲染终态审核记录并给出用户可读状态文案', async () => {
    vi.spyOn(reviewsApi, 'fetchReviewHistory').mockResolvedValue({
      items: [item(), item({ review_id: 43, status: 'rejected', title: '' })],
      next_cursor: null,
    });
    renderHistory();
    await waitFor(() => expect(screen.getByText('已审投稿')).toBeTruthy());
    expect(screen.getByText('审核 #43')).toBeTruthy();
    expect(screen.getAllByText(/已发布/).length).toBeGreaterThan(0);
    expect(screen.getAllByText(/已拒绝/).length).toBeGreaterThan(0);
  });

  it('空历史给出明确的空态文案，而不是永远加载', async () => {
    vi.spyOn(reviewsApi, 'fetchReviewHistory').mockResolvedValue({ items: [], next_cursor: null });
    renderHistory();
    await waitFor(() => expect(screen.getByText('还没有审核历史记录。')).toBeTruthy());
  });

  it('存在下一页游标时展示加载更多入口', async () => {
    const spy = vi
      .spyOn(reviewsApi, 'fetchReviewHistory')
      .mockResolvedValueOnce({ items: [item()], next_cursor: 'cursor-2' })
      .mockResolvedValueOnce({ items: [item({ review_id: 44, title: '更早的记录' })], next_cursor: null });
    renderHistory();
    await waitFor(() => expect(screen.getByText('加载更多')).toBeTruthy());
    screen.getByText('加载更多').click();
    await waitFor(() => expect(screen.getByText('更早的记录')).toBeTruthy());
    expect(spy).toHaveBeenCalledTimes(2);
  });

  it('刷新频率属于实现细节，不再出现在标题里', async () => {
    vi.spyOn(reviewsApi, 'fetchReviewHistory').mockResolvedValue({
      items: [item()],
      next_cursor: null,
    });
    renderHistory();
    await waitFor(() => expect(screen.getByText('已审投稿')).toBeTruthy());
    expect(document.body.textContent).not.toContain('自动刷新');
  });
});
