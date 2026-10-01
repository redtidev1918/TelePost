import { useBotNavigate } from '../../lib/useBotNavigate';
import { Cell, Section, Spinner } from '@telegram-apps/telegram-ui';
import { useInfiniteQuery } from '@tanstack/react-query';
import { fetchReviewHistory, ReviewSummary } from '../../api/reviews';

/** User-facing review outcome labels; DB states never leak verbatim. */
const HISTORY_LABELS: Record<string, string> = {
  published: '已发布',
  rejected: '已拒绝',
  failed: '失败',
  expired: '已过期',
  superseded: '已被替换',
};

/**
 * 审核历史 (§history): the reviewer's read-only history of terminal review
 * outcomes for the CURRENT bot scope. Same ReviewService / persisted review
 * state as the queue — the Mini App never invents a second review store.
 */
export function ReviewHistoryPage() {
  const navigate = useBotNavigate();
  const query = useInfiniteQuery({
    queryKey: ['review-history'],
    queryFn: ({ pageParam }) => fetchReviewHistory(pageParam),
    initialPageParam: null as string | null,
    getNextPageParam: (last) => last.next_cursor,
    refetchInterval: 30_000,
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
    return <div className="page-empty">还没有审核历史记录。</div>;
  }

  return (
    <Section header="审核历史（每 30 秒自动刷新）">
      {items.map((item) => (
        <Cell
          key={item.review_id}
          onClick={() => navigate(`/review/${item.review_id}`)}
          subtitle={
            <>
              {(HISTORY_LABELS[item.status] || item.status)}
              {item.tags.length ? ` · ${item.tags.slice(0, 3).join(' ')}` : ''}
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
