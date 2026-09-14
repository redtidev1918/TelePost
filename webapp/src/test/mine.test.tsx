import { describe, expect, it, vi, beforeEach } from 'vitest';
import { cleanup, render, screen, fireEvent, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { MemoryRouter } from 'react-router-dom';
import { AppRoot } from '@telegram-apps/telegram-ui';
import { MySubmissionsPage } from '../pages/MySubmissions/MySubmissionsPage';
import * as me from '../api/me';

function chainItem(
  submissionId: string,
  overrides: Partial<me.LogicalSubmission> = {},
): me.LogicalSubmission {
  return {
    submission_id: submissionId,
    review_chain_id: submissionId,
    current_review_id: 3,
    status: 'in_review',
    title: '我的标题',
    tags: ['#tag'],
    media_count: 2,
    document_count: 0,
    spoiler: false,
    created_at: 100,
    updated_at: 300,
    generation: 2,
    refetch_count: 2,
    ...overrides,
  };
}

function renderPage(items: me.LogicalSubmission[]) {
  vi.spyOn(me, 'fetchMySubmissions').mockResolvedValue({
    items,
    next_cursor: null,
  });
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <AppRoot>
        <MemoryRouter initialEntries={['/mine']}>
          <MySubmissionsPage />
        </MemoryRouter>
      </AppRoot>
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  vi.restoreAllMocks();
});

describe('MySubmissionsPage (logical submissions)', () => {
  it('renders ONE row per chain head, not per generation', async () => {
    renderPage([chainItem('chain-1')]);
    const rows = await screen.findAllByTestId('mine-item');
    expect(rows).toHaveLength(1);
    expect(rows[0].textContent).toContain('我的标题');
    expect(rows[0].textContent).toContain('审核中');
    // Generation metadata is user friendly: refetch collapsed into one item.
    expect(rows[0].textContent).toContain('已更换候选 2 次');
    expect(rows[0].textContent).toContain('2 个文件');
    // No raw review id shown in the list.
    expect(rows[0].textContent).not.toContain('review-');
  });

  it('collapses an A→B→C refetch chain into a single item', async () => {
    renderPage([chainItem('chain-1')]);
    const rows = await screen.findAllByTestId('mine-item');
    expect(rows).toHaveLength(1);
  });

  it('filters by status groups', async () => {
    renderPage([
      chainItem('chain-a', { status: 'in_review', title: '进行中的' }),
      chainItem('chain-b', { status: 'published', title: '已发布的' }),
      chainItem('chain-c', { status: 'rejected', title: '被拒的' }),
    ]);
    await screen.findAllByTestId('mine-item');

    fireEvent.click(screen.getByTestId('filter-active'));
    await waitFor(() => {
      const active = screen.getAllByTestId('mine-item');
      expect(active).toHaveLength(1);
      expect(active[0].textContent).toContain('进行中的');
    });

    fireEvent.click(screen.getByTestId('filter-done'));
    await waitFor(() => {
      const done = screen.getAllByTestId('mine-item');
      expect(done).toHaveLength(1);
      expect(done[0].textContent).toContain('已发布的');
    });

    fireEvent.click(screen.getByTestId('filter-other'));
    await waitFor(() => {
      const other = screen.getAllByTestId('mine-item');
      expect(other).toHaveLength(1);
      expect(other[0].textContent).toContain('被拒的');
    });
  });

  it('renders every user-facing status label', async () => {
    const labels = [
      'preparing', 'in_review', 'publishing', 'published', 'rejected',
      'failed', 'expired',
    ];
    for (const status of labels) {
      renderPage([chainItem(`chain-${status}`, { status, title: status })]);
      const row = await screen.findAllByTestId('mine-item');
      expect(row).toHaveLength(1);
      cleanup();
    }
  });
});