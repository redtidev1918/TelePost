import { useBotNavigate } from '../../lib/useBotNavigate';
import { useState } from 'react';
import { Badge, Button, Cell, Section, Spinner } from '@telegram-apps/telegram-ui';
import { useInfiniteQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import {
  canDeleteHistory,
  deleteAllMySubmissions,
  deleteMySubmission,
  fetchMySubmissions,
  LogicalSubmission,
  matchesFilter,
  MineFilter,
} from '../../api/me';

/** User-facing status text (§38): database states never reach the list. */
const STATUS_LABELS: Record<string, string> = {
  preparing: '准备中',
  in_review: '审核中',
  publishing: '发布中',
  published: '已发布',
  rejected: '未通过',
  failed: '处理失败',
  expired: '已过期',
};

const FILTERS: { key: MineFilter; label: string }[] = [
  { key: 'all', label: '全部' },
  { key: 'active', label: '进行中' },
  { key: 'done', label: '已完成' },
  { key: 'other', label: '其他' },
];

function formatDay(seconds: number): string {
  if (!seconds) return '';
  const date = new Date(seconds * 1000);
  const today = new Date();
  const time = `${String(date.getHours()).padStart(2, '0')}:${String(
    date.getMinutes(),
  ).padStart(2, '0')}`;
  if (date.toDateString() === today.toDateString()) return `今天 ${time}`;
  return `${date.getMonth() + 1}月${date.getDate()}日 ${time}`;
}

function StatusBadge({ status }: { status: string }) {
  const label = STATUS_LABELS[status] || status;
  const mode = status === 'published'
    ? 'primary'
    : status === 'rejected' || status === 'failed'
      ? 'critical'
      : 'secondary';
  return <Badge type="number" mode={mode}>{label}</Badge>;
}

/**
 * 我的投稿 (§mine): the verified user's LOGICAL submissions, one row per review
 * chain. Refetch generations collapse server-side, so a replacement never shows
 * up as an extra item, and service/automatic submissions never appear at all.
 */
export function MySubmissionsPage() {
  const navigate = useBotNavigate();
  const qc = useQueryClient();
  const [filter, setFilter] = useState<MineFilter>('all');
  const clearHistory = useMutation({
    mutationFn: () => deleteAllMySubmissions(),
    onSuccess: () => void qc.invalidateQueries({ queryKey: ['my-submissions'] }),
  });
  const delHistory = useMutation({
    mutationFn: (reviewId: number | string) => deleteMySubmission(reviewId),
    onSuccess: () => void qc.invalidateQueries({ queryKey: ['my-submissions'] }),
  });
  const query = useInfiniteQuery({
    queryKey: ['my-submissions'],
    queryFn: ({ pageParam }) => fetchMySubmissions(pageParam),
    initialPageParam: null as string | null,
    getNextPageParam: (last) => last.next_cursor,
  });

  if (query.isLoading) {
    return (
      <div className="page-loading">
        <Spinner size="m" />
      </div>
    );
  }
  if (query.isError) {
    return (
      <div className="page-error">
        加载失败：{(query.error as Error).message}
      </div>
    );
  }
  const all: LogicalSubmission[] = query.data?.pages.flatMap((p) => p.items) ?? [];
  const items = all.filter((item) => matchesFilter(item.status, filter));

  return (
    <div>
      <Section header="我的投稿">
        <div className="mine-filters" data-testid="mine-filters">
          {FILTERS.map((entry) => (
            <button
              key={entry.key}
              type="button"
              data-testid={`filter-${entry.key}`}
              className={filter === entry.key ? 'mine-filter mine-filter--on' : 'mine-filter'}
              onClick={() => setFilter(entry.key)}
            >
              {entry.label}
            </button>
          ))}
        </div>
        {all.length > 0 && (
          <div style={{ display: 'flex', justifyContent: 'flex-end', marginBottom: 8 }}>
            <Button
              size="s"
              mode="plain"
              loading={clearHistory.isPending}
              data-testid="mine-clear-history"
              onClick={() => {
                if (window.confirm('隐藏所有已结束的投稿记录？频道内容不受影响。')) {
                  void clearHistory.mutate();
                }
              }}
            >
              清空已结束记录
            </Button>
          </div>
        )}
        {all.length === 0 && (
          <div className="page-empty">还没有投稿，去「投稿」页发一条吧。</div>
        )}
        {all.length > 0 && items.length === 0 && (
          <div className="page-empty">该分类下暂无投稿。</div>
        )}
        {items.map((item) => (
          <Cell
            key={item.review_chain_id || item.submission_id}
            data-testid="mine-item"
            onClick={() => navigate(`/mine/${item.current_review_id}`)}
            after={
              <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
                <StatusBadge status={item.status} />
                {canDeleteHistory(item.status) && (
                  <Button
                    size="s"
                    mode="outline"
                    loading={delHistory.isPending && delHistory.variables === item.current_review_id}
                    onClick={(e) => {
                      e.stopPropagation();
                      void delHistory.mutate(item.current_review_id);
                    }}
                    data-testid={`mine-delete-${item.current_review_id}`}
                  >
                    删除
                  </Button>
                )}
              </div>
            }
            subtitle={
              <div className="mine-meta">
                {item.media_count + item.document_count > 0 &&
                  `${item.media_count + item.document_count} 个文件 · `}
                {formatDay(item.updated_at)}
                {item.refetch_count > 0 && ` · 已重抓/换图 ${item.refetch_count} 次`}
              </div>
            }
          >
            {item.title || '未命名投稿'}
          </Cell>
        ))}
        {query.hasNextPage && filter === 'all' && (
          <Cell
            data-testid="load-more"
            onClick={() => void query.fetchNextPage()}
            after="↓"
          >
            加载更多
          </Cell>
        )}
      </Section>
    </div>
  );
}