import { useState } from 'react';
import { Button, Spinner } from '@telegram-apps/telegram-ui';
import { useInfiniteQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { useBotNavigate } from '../../lib/useBotNavigate';
import {
  canDeleteHistory,
  deleteAllMySubmissions,
  deleteMySubmission,
  fetchMySubmissions,
  LogicalSubmission,
  matchesFilter,
  MineFilter,
} from '../../api/me';
import { PageHeader } from '../../components/ui/PageHeader';
import { Segmented } from '../../components/ui/Segmented';
import { StatusBadge, StatusTone } from '../../components/ui/StatusBadge';
import { EmptyState } from '../../components/ui/EmptyState';

/**
 * 我的投稿 (§mine): the verified user's LOGICAL submissions, one row per review
 * chain, presented as personal content history — not a database table.
 *
 * Destructive and low-frequency actions (delete a row, clear finished history)
 * live behind overflow controls instead of sitting on every row, but they keep
 * the exact same server semantics: only the owner's own deletable history is
 * ever hidden, and real channel content is never touched.
 */

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

const STATUS_TONES: Record<string, StatusTone> = {
  published: 'good',
  rejected: 'bad',
  failed: 'bad',
  preparing: 'progress',
  in_review: 'progress',
  publishing: 'progress',
};

const FILTERS: { value: MineFilter; label: string }[] = [
  { value: 'all', label: '全部' },
  { value: 'active', label: '进行中' },
  { value: 'done', label: '已完成' },
  { value: 'other', label: '其他' },
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

function SubmissionRow({ item, onOpen }: { item: LogicalSubmission; onOpen: () => void }) {
  const [open, setOpen] = useState(false);
  const qc = useQueryClient();
  const del = useMutation({
    mutationFn: () => deleteMySubmission(item.current_review_id),
    onSuccess: () => void qc.invalidateQueries({ queryKey: ['my-submissions'] }),
  });
  const deletable = canDeleteHistory(item.status);
  const fileCount = item.media_count + item.document_count;

  return (
    <div className="card">
      <div
        className="card__row"
        role="button"
        tabIndex={0}
        data-testid="mine-item"
        onClick={onOpen}
        onKeyDown={(event) => {
          if (event.key === 'Enter' || event.key === ' ') {
            event.preventDefault();
            onOpen();
          }
        }}
      >
        <div className="card__row-title">{item.title || '未命名投稿'}</div>
        <div className="card__row-meta">
          {fileCount > 0 ? `${fileCount} 个文件 · ` : ''}
          {formatDay(item.updated_at)}
          {item.refetch_count > 0 ? ` · 已重抓/换图 ${item.refetch_count} 次` : ''}
        </div>
        <div className="card__row-foot">
          <StatusBadge tone={STATUS_TONES[item.status] ?? 'neutral'}>
            {STATUS_LABELS[item.status] || item.status}
          </StatusBadge>
          {deletable ? (
            <button
              type="button"
              className="page-section__action"
              aria-label="更多操作"
              data-testid={`mine-row-more-${item.current_review_id}`}
              onClick={(event) => {
                event.stopPropagation();
                setOpen((value) => !value);
              }}
            >
              ⋯
            </button>
          ) : null}
        </div>
      </div>
      {deletable ? (
        <div className={`overflow-panel${open ? ' overflow-panel--open' : ''}`}>
          <div className="card__row card__row--static">
            <Button
              size="s"
              mode="plain"
              stretched
              style={{ color: 'var(--tgui--destructive_text_color)' }}
              loading={del.isPending}
              onClick={() => void del.mutateAsync()}
              data-testid={`mine-delete-${item.current_review_id}`}
            >
              删除这条记录
            </Button>
          </div>
        </div>
      ) : null}
    </div>
  );
}

export function MySubmissionsPage() {
  const navigate = useBotNavigate();
  const qc = useQueryClient();
  const [filter, setFilter] = useState<MineFilter>('all');
  const [moreOpen, setMoreOpen] = useState(false);

  const clearHistory = useMutation({
    mutationFn: () => deleteAllMySubmissions(),
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
      <div className="stack">
        <PageHeader title="我的投稿" />
        <EmptyState title="暂时无法加载投稿记录" hint={(query.error as Error).message} />
      </div>
    );
  }

  const all: LogicalSubmission[] = query.data?.pages.flatMap((p) => p.items) ?? [];
  const items = all.filter((item) => matchesFilter(item.status, filter));

  return (
    <div className="stack">
      <PageHeader
        title="我的投稿"
        subtitle={all.length > 0 ? `共 ${all.length} 条记录` : undefined}
        action={
          all.length > 0 ? (
            <button
              type="button"
              className="page-section__action"
              data-testid="mine-more"
              onClick={() => setMoreOpen((value) => !value)}
            >
              更多
            </button>
          ) : null
        }
      />

      {all.length === 0 ? (
        <EmptyState
          title="还没有投稿"
          hint="发布第一条内容后，这里会保留你的投稿进度和结果。"
          action={
            <Button size="m" stretched onClick={() => navigate('/submit')}>
              去投稿
            </Button>
          }
        />
      ) : (
        <>
          <Segmented<MineFilter>
            testId="filter"
            value={filter}
            options={FILTERS}
            onChange={setFilter}
          />
          <div className={`overflow-panel${moreOpen ? ' overflow-panel--open' : ''}`}>
            <div className="card">
              <div className="card__row card__row--static">
                <Button
                  size="s"
                  mode="plain"
                  stretched
                  style={{ color: 'var(--tgui--destructive_text_color)' }}
                  loading={clearHistory.isPending}
                  data-testid="mine-clear-history"
                  onClick={() => {
                    if (window.confirm('隐藏所有已结束的投稿记录？频道内容不受影响。')) {
                      void clearHistory.mutate();
                    }
                  }}
                >
                  清理已结束的记录
                </Button>
                <div className="mutation-help" style={{ marginTop: 6 }}>
                  只隐藏你自己的历史记录，不会删除频道里已发布的内容。
                </div>
              </div>
            </div>
          </div>

          {items.length === 0 ? (
            <EmptyState title="该分类下暂无投稿" />
          ) : (
            <div className="stack">
              {items.map((item) => (
                <SubmissionRow
                  key={item.review_chain_id || item.submission_id}
                  item={item}
                  onOpen={() => navigate(`/mine/${item.current_review_id}`)}
                />
              ))}
            </div>
          )}

          {query.hasNextPage && filter === 'all' && (
            <Button
              mode="bezeled"
              stretched
              data-testid="load-more"
              loading={query.isFetchingNextPage}
              onClick={() => void query.fetchNextPage()}
            >
              加载更多
            </Button>
          )}
          <div className="mutation-help">点击一条记录可以查看详情、重投或编辑记录。</div>
        </>
      )}
    </div>
  );
}
