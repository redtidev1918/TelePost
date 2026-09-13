import { describe, expect, it, vi, beforeEach } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import { AppRoot } from '@telegram-apps/telegram-ui';
import { ReviewDetailPage } from '../pages/ReviewDetail/ReviewDetailPage';
import * as reviewsApi from '../api/reviews';

beforeEach(() => {
  vi.restoreAllMocks();
});

function renderDetail() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(
    <QueryClientProvider client={qc}>
      <AppRoot appearance="light">
        <MemoryRouter initialEntries={['/review/7']}>
          <Routes>
            <Route path="/review/:id" element={<ReviewDetailPage />} />
          </Routes>
        </MemoryRouter>
      </AppRoot>
    </QueryClientProvider>,
  );
}

describe('ReviewDetailPage', () => {
  it('renders review metadata + approve action for a reviewer', async () => {
    vi.spyOn(reviewsApi, 'fetchReview').mockResolvedValue({
      id: 7,
      status: 'pending',
      title: '测试投稿',
      note: '备注内容',
      tags: ['#a', '#b'],
      link: 'https://example.com',
      anonymous: false,
      spoiler: false,
      submitter_name: 'alice',
      submitter_id: 1,
      source_label: 'Telegram',
      source_ref: null,
      scheduled_at: null,
      source: 'api',
      target_id: 't',
      media: [],
      created_at: '2026-01-01T00:00:00Z',
      updated_at: '2026-01-01T00:00:00Z',
      error: '',
    });
    vi.spyOn(reviewsApi, 'fetchRefetchAttempt').mockResolvedValue({
      attempt: null,
      lineage: [],
    });
    vi.spyOn(reviewsApi, 'approveReview').mockResolvedValue({
      review_id: 7,
      status: 'published',
      reused: false,
      message_id: 1,
      link: 'https://t.me/c/1/1',
    });
    renderDetail();
    await waitFor(() => expect(screen.getByText(/测试投稿/)).toBeInTheDocument());
    expect(screen.getByText(/待审核/)).toBeInTheDocument();
    const approve = screen.getByRole('button', { name: /通过/ });
    await userEvent.click(approve);
    await waitFor(() => expect(reviewsApi.approveReview).toHaveBeenCalledWith('7'));
  });
});
