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
      <AppRoot appearance="light" platform="ios">
        <MemoryRouter initialEntries={['/review/7']}>
          <Routes>
            <Route path="/review/:id" element={<ReviewDetailPage />} />
          </Routes>
        </MemoryRouter>
      </AppRoot>
    </QueryClientProvider>,
  );
  return qc;
}

describe('ReviewDetailPage', () => {
  function mockReview() {
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
  }

  it('renders review metadata + approve action for a reviewer', async () => {
    mockReview();
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

  it('refreshes detail and other cached views after approval, exposing the channel without a source', async () => {
    mockReview();
    const pending = await reviewsApi.fetchReview(7);
    vi.mocked(reviewsApi.fetchReview).mockResolvedValueOnce(pending).mockResolvedValue({
      ...pending, status: 'published', link: '', channel_url: 'https://t.me/example_channel/81',
    });
    vi.spyOn(reviewsApi, 'fetchRefetchAttempt').mockResolvedValue({ attempt: null, lineage: [] });
    vi.spyOn(reviewsApi, 'approveReview').mockResolvedValue({
      review_id: 7, status: 'published', reused: false, message_id: 81, link: 'https://t.me/example_channel/81',
    });
    const qc = renderDetail();
    for (const key of ['review-history', 'review-history-peek', 'my-submissions', 'my-submission', 'hot-posts']) {
      qc.setQueryData([key], {});
    }
    await userEvent.click(await screen.findByTestId('approve'));
    expect(await screen.findByTestId('review-channel')).toBeInTheDocument();
    expect(screen.queryByTestId('approve')).toBeNull();
    expect(screen.getByText('已发布')).toBeInTheDocument();
    for (const key of ['review-history', 'review-history-peek', 'my-submissions', 'my-submission', 'hot-posts']) {
      expect(qc.getQueryState([key])?.isInvalidated).toBe(true);
    }
  });

  it('refreshes actual state after a failed concurrent action', async () => {
    mockReview();
    const pending = await reviewsApi.fetchReview(7);
    vi.mocked(reviewsApi.fetchReview).mockResolvedValueOnce(pending).mockResolvedValue({
      ...pending, status: 'rejected',
    });
    vi.spyOn(reviewsApi, 'fetchRefetchAttempt').mockResolvedValue({ attempt: null, lineage: [] });
    vi.spyOn(reviewsApi, 'approveReview').mockRejectedValue(new Error('already resolved'));
    renderDetail();
    await userEvent.click(await screen.findByTestId('approve'));
    expect(await screen.findByText('已拒绝')).toBeInTheDocument();
    expect(screen.queryByTestId('approve')).toBeNull();
    expect(screen.queryByTestId('review-channel')).toBeNull();
  });

  // §refetch-lifecycle: the durable job id, the stage the remote state proves,
  // and the elapsed wait must all be visible, and an active canonical state must
  // keep the button disabled even though the wire still reports legacy `admitted`.
  it('shows the refetch job id, stage and elapsed wait while active', async () => {
    mockReview();
    vi.spyOn(reviewsApi, 'fetchRefetchAttempt').mockResolvedValue({
      attempt: {
        request_id: 'req-1',
        state: 'admitted',
        canonical_state: 'searching',
        stage: 'SEARCHING',
        label: '正在重抓：搜索候选',
        task_id: 'refetch-7-1758000000',
        progress: {
          task_id: 'refetch-7-1758000000',
          stage: 'SEARCHING',
          label: '正在重抓：搜索候选',
          elapsed_seconds: 200,
        },
        generation: 2,
        source_review_id: 7,
        result_candidate_id: '',
        slot_id: '',
        failure_code: '',
        scanned: 0,
        skipped_duplicate: 0,
        skipped_invalid: 0,
        skipped_unavailable: 0,
        created_at: 1758000000,
        finished_at: null,
      },
      lineage: [
        { generation: 1, candidate_id: '111', status: 'obsolete', outcome: 'kept' },
        { generation: 2, candidate_id: '222', status: 'current' },
      ],
    });
    renderDetail();
    await waitFor(() => expect(screen.getByText('任务ID')).toBeInTheDocument());
    expect(screen.getByText('refetch-7-1758000000')).toBeInTheDocument();
    expect(screen.getByText('正在重抓：搜索候选')).toBeInTheDocument();
    expect(screen.getByText('已等待 3 分 20 秒')).toBeInTheDocument();
    expect(screen.getByText('G1:111 → G2:222')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /重抓/ })).toBeDisabled();
  });

  it('keeps the refetch button usable once the attempt reached a terminal state', async () => {
    mockReview();
    vi.spyOn(reviewsApi, 'fetchRefetchAttempt').mockResolvedValue({
      attempt: {
        request_id: 'req-2',
        state: 'no_alternative',
        canonical_state: 'no_candidate',
        label: '没有新的可替换作品',
        task_id: 'refetch-7-1758000120',
        terminal_reason: 'no_candidate',
        generation: 1,
        source_review_id: 7,
        result_candidate_id: '',
        slot_id: '',
        failure_code: '',
        scanned: 12,
        skipped_duplicate: 0,
        skipped_invalid: 0,
        skipped_unavailable: 0,
        created_at: 1758000120,
        finished_at: 1758000200,
      },
      lineage: [],
    });
    renderDetail();
    await waitFor(() => expect(screen.getByText('没有新的可替换作品')).toBeInTheDocument());
    expect(screen.queryByText(/已等待/)).not.toBeInTheDocument();
    expect(screen.getByRole('button', { name: /重抓/ })).not.toBeDisabled();
  });
});
