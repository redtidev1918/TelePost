import { describe, expect, it, vi, beforeEach } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import { AppRoot } from '@telegram-apps/telegram-ui';
import { ReviewQueuePage } from '../pages/ReviewQueue/ReviewQueuePage';
import * as reviewsApi from '../api/reviews';

beforeEach(() => {
  vi.restoreAllMocks();
});

function renderQueue() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(
    <QueryClientProvider client={qc}>
      <AppRoot appearance="light">
        <MemoryRouter initialEntries={['/review']}>
          <Routes>
            <Route path="/review" element={<ReviewQueuePage />} />
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

function mockQueue(items: reviewsApi.ReviewSummary[]) {
  vi.spyOn(reviewsApi, 'fetchReviewQueue').mockResolvedValue({
    items,
    next_cursor: null,
  });
}

describe('ReviewQueuePage', () => {
  it('有待审条目时正常渲染队列', async () => {
    mockQueue([item()]);
    renderQueue();
    await waitFor(() => expect(screen.getByText('待审投稿')).toBeTruthy());
    expect(screen.queryByText('当前没有待审核内容')).toBeNull();
  });

  it('空队列时展示最近处理记录，解释「为什么空」(§review-queue-empty-state)', async () => {
    mockQueue([]);
    vi.spyOn(reviewsApi, 'fetchReviewHistory').mockResolvedValue({
      items: [
        item({ review_id: 41, title: '昨天那条', status: 'published' }),
        item({ review_id: 40, title: '', status: 'expired' }),
      ],
      next_cursor: null,
    });
    renderQueue();
    await waitFor(() => expect(screen.getByText('当前没有待审核内容')).toBeTruthy());
    await waitFor(() => expect(screen.getByText('昨天那条')).toBeTruthy());
    expect(screen.getByText('已发布')).toBeTruthy();
    expect(screen.getByText('审核 #40')).toBeTruthy();
    expect(screen.getByText('已过期')).toBeTruthy();
    expect(screen.getByText('查看审核历史')).toBeTruthy();
  });

  it('历史窥探失败时空态保持可用，绝不变成错误态', async () => {
    mockQueue([]);
    vi.spyOn(reviewsApi, 'fetchReviewHistory').mockRejectedValue(new Error('boom'));
    renderQueue();
    await waitFor(() => expect(screen.getByText('当前没有待审核内容')).toBeTruthy());
    expect(screen.queryByText(/加载失败/)).toBeNull();
    expect(screen.queryByText('查看审核历史')).toBeNull();
  });

  it('历史也为空时只显示空态文案', async () => {
    mockQueue([]);
    vi.spyOn(reviewsApi, 'fetchReviewHistory').mockResolvedValue({
      items: [],
      next_cursor: null,
    });
    renderQueue();
    await waitFor(() => expect(screen.getByText('当前没有待审核内容')).toBeTruthy());
    expect(screen.queryByText('最近处理')).toBeNull();
  });

  it('刷新频率属于实现细节，不再出现在标题里', async () => {
    mockQueue([item()]);
    renderQueue();
    await waitFor(() => expect(screen.getByText('待审投稿')).toBeTruthy());
    expect(document.body.textContent).not.toContain('自动刷新');
  });
});
