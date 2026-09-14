import { useBotNavigate } from '../../lib/useBotNavigate';
import { Cell, Section, Spinner } from '@telegram-apps/telegram-ui';
import { useInfiniteQuery } from '@tanstack/react-query';
import { fetchReviewQueue, ReviewSummary } from '../../api/reviews';

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
    return <div className="page-empty">审核队列为空。</div>;
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
