import { Button, Spinner } from '@telegram-apps/telegram-ui';
import { useInfiniteQuery, useQuery } from '@tanstack/react-query';
import { useBotNavigate } from '../../lib/useBotNavigate';
import { reviewStatusLabel } from '../../lib/reviewStatus';
import { fetchReviewHistory, fetchReviewQueue, ReviewSummary } from '../../api/reviews';
import { StatusBadge } from '../../components/ui/StatusBadge';
import { EmptyState } from '../../components/ui/EmptyState';
import { PageSection } from '../../components/ui/PageSection';

/**
 * 审核 · 待处理 (§review-workspace).
 *
 * The queue only ever holds LIVE pending rows: a submission is visible only
 * between "preview + control card posted to the review group" and
 * "approved / rejected / expired". Review action happens primarily on the
 * Telegram review-group control cards, so the queue is legitimately empty most
 * of the time — which used to read as "the queue is broken".
 *
 * Two presentation rules:
 * - refresh cadence is an IMPLEMENTATION detail and is never printed;
 * - an empty queue explains itself (recent outcomes + history entry) instead of
 *   looking like a failure. The peek is best-effort: its failure must never turn
 *   the empty state into an error state.
 */

function QueueEmptyState() {
  const navigate = useBotNavigate();
  const peek = useQuery({
    queryKey: ['review-history-peek'],
    queryFn: () => fetchReviewHistory(null, 3),
    refetchInterval: 30_000,
    retry: false,
  });
  const recent = peek.data?.items ?? [];
  return (
    <div className="stack">
      <EmptyState
        title="当前没有待审核内容"
        hint="新的投稿会先出现在 Telegram 审核群里，处理完成后进入审核历史。"
      />
      {recent.length > 0 && (
        <PageSection title="最近处理">
          <div className="card">
            {recent.map((item) => (
              <div
                key={item.review_id}
                className="card__row"
                role="button"
                tabIndex={0}
                onClick={() => navigate(`/review/${item.review_id}`)}
                onKeyDown={(event) => {
                  if (event.key === 'Enter' || event.key === ' ') {
                    event.preventDefault();
                    navigate(`/review/${item.review_id}`);
                  }
                }}
              >
                <div className="card__row-title">{item.title || `审核 #${item.review_id}`}</div>
                <div className="card__row-meta">{reviewStatusLabel(item.status)}</div>
              </div>
            ))}
          </div>
        </PageSection>
      )}
      {recent.length > 0 && (
        <Button mode="outline" stretched onClick={() => navigate('/review/history')}>
          查看审核历史
        </Button>
      )}
    </div>
  );
}

export function ReviewQueuePage() {
  const navigate = useBotNavigate();
  const query = useInfiniteQuery({
    queryKey: ['review-queue'],
    queryFn: ({ pageParam }) => fetchReviewQueue(pageParam),
    initialPageParam: null as string | null,
    getNextPageParam: (last) => last.next_cursor,
    refetchInterval: 15_000, // §37: first version polls, no WebSocket/SSE
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
      <EmptyState
        title="暂时无法加载待审队列"
        hint={(query.error as Error).message}
        action={
          <Button size="m" stretched onClick={() => void query.refetch()}>
            重试
          </Button>
        }
      />
    );
  }
  const items: ReviewSummary[] = query.data?.pages.flatMap((p) => p.items) ?? [];
  if (items.length === 0) {
    return <QueueEmptyState />;
  }

  return (
    <div className="stack">
      <PageSection title={items.length > 1 ? `待处理 ${items.length} 条` : '待处理'}>
        <div className="card" data-testid="review-queue-list">
          {items.map((item) => (
            <div
              key={item.review_id}
              className="card__row"
              role="button"
              tabIndex={0}
              data-testid="review-queue-item"
              onClick={() => navigate(`/review/${item.review_id}`)}
              onKeyDown={(event) => {
                if (event.key === 'Enter' || event.key === ' ') {
                  event.preventDefault();
                  navigate(`/review/${item.review_id}`);
                }
              }}
            >
              <div className="card__row-title">{item.title || `审核 #${item.review_id}`}</div>
              <div className="card__row-meta">
                {[
                  item.tags.slice(0, 3).join(' '),
                  item.media_count ? `${item.media_count} 个附件` : '',
                  item.spoiler ? '剧透' : '',
                ].filter(Boolean).join(' · ')}
              </div>
              <div className="card__row-foot">
                <StatusBadge tone="progress">待审核</StatusBadge>
              </div>
            </div>
          ))}
        </div>
      </PageSection>
      {query.hasNextPage && (
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
    </div>
  );
}
