import { useBotNavigate } from '../../lib/useBotNavigate';
import { reviewStatusLabel } from '../../lib/reviewStatus';
import { Cell, Section, Spinner } from '@telegram-apps/telegram-ui';
import { useInfiniteQuery, useQuery } from '@tanstack/react-query';
import { fetchReviewHistory, fetchReviewQueue, ReviewSummary } from '../../api/reviews';

/**
 * Empty-queue context (§review-queue-empty-state).
 *
 * The queue only ever holds LIVE pending rows: a submission is visible only
 * between "preview + control card posted to the review group" and
 * "approved / rejected / expired". Review action happens primarily on the
 * Telegram review-group control cards, so the queue is legitimately empty
 * most of the time — which read as "the queue is broken". When it is empty,
 * show the most recent terminal outcomes (same ReviewService store, reviewer
 * scope) so the reviewer can SEE that flow happened, and offer the history
 * page as the record surface. The peek is strictly best-effort: its failure
 * must never turn the empty state into an error state.
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
    <Section>
      <div className="page-empty">审核队列为空。</div>
      {recent.length > 0 && (
        <Section header="最近处理">
          {recent.map((item) => (
            <Cell
              key={item.review_id}
              onClick={() => navigate(`/review/${item.review_id}`)}
              subtitle={reviewStatusLabel(item.status)}
            >
              {item.title || `审核 #${item.review_id}`}
            </Cell>
          ))}
          <Cell onClick={() => navigate('/review/history')} after="→">
            查看全部审核历史
          </Cell>
        </Section>
      )}
    </Section>
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
    return <div className="page-error">加载失败：{(query.error as Error).message}</div>;
  }
  const items: ReviewSummary[] = query.data?.pages.flatMap((p) => p.items) ?? [];
  if (items.length === 0) {
    return <QueueEmptyState />;
  }

  return (
    <Section header="审核队列（每 15 秒自动刷新）">
      {items.map((item) => (
        <Cell
          key={item.review_id}
          onClick={() => navigate(`/review/${item.review_id}`)}
          subtitle={
            <>
              {item.tags.slice(0, 3).join(' ')}
              {item.media_count ? ` · 📎 ${item.media_count}` : ''}
              {item.spoiler ? ' · 🫥' : ''}
            </>
          }
        >
          {item.title || `审核 #${item.review_id}`}
        </Cell>
      ))}
      {query.hasNextPage && (
        <Cell onClick={() => void query.fetchNextPage()} after="↓">
          加载更多
        </Cell>
      )}
    </Section>
  );
}
