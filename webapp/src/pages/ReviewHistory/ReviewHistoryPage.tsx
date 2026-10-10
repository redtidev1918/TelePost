import { tr } from "../../lib/i18n";
import { useTranslation } from 'react-i18next';
import { Button, Spinner } from '@telegram-apps/telegram-ui';
import { useInfiniteQuery } from '@tanstack/react-query';
import { useBotNavigate } from '../../lib/useBotNavigate';
import { fetchReviewHistory, ReviewSummary } from '../../api/reviews';
import { StatusBadge, StatusTone } from '../../components/ui/StatusBadge';
import { EmptyState } from '../../components/ui/EmptyState';
import { PageSection } from '../../components/ui/PageSection';

/**
 * 审核 · 历史 (§history): the reviewer's read-only history of terminal review
 * outcomes for the CURRENT bot scope. Same ReviewService / persisted review
 * state as the queue — the Mini App never invents a second review store.
 */

const TONES: Record<string, StatusTone> = {
  published: 'good',
  rejected: 'bad',
  failed: 'bad',
};

const LABELS = (): Record<string, string> => ({
  published: tr("已发布"),
  rejected: tr("已拒绝"),
  failed: tr("失败"),
  expired: tr("已过期"),
  superseded: tr("已被替换"),
});

export function ReviewHistoryPage() {
  useTranslation();
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
    return (
      <EmptyState
        title={tr("暂时无法加载审核历史")}
        hint={(query.error as Error).message}
        action={
          <Button size="m" stretched onClick={() => void query.refetch()}>
            {tr("重试")}</Button>
        }
      />
    );
  }
  const items: ReviewSummary[] = query.data?.pages.flatMap((p) => p.items) ?? [];
  if (items.length === 0) {
    return <EmptyState title={tr("还没有审核历史记录。")} hint={tr("处理过的投稿会按时间出现在这里。")} />;
  }

  return (
    <div className="stack">
      <PageSection title={tr("审核历史")}>
        <div className="card">
          {items.map((item) => (
            <div
              key={item.review_id}
              className="card__row"
              role="button"
              tabIndex={0}
              data-testid="review-history-item"
              onClick={() => navigate(`/review/${item.review_id}`)}
              onKeyDown={(event) => {
                if (event.key === 'Enter' || event.key === ' ') {
                  event.preventDefault();
                  navigate(`/review/${item.review_id}`);
                }
              }}
            >
              <div className="card__row-title">{item.title || tr("审核 #{{p0}}", {p0: item.review_id})}</div>
              <div className="card__row-meta">
                {[
                  item.tags.length ? item.tags.slice(0, 3).join(' ') : '',
                  item.media_count ? tr("{{p0}} 个附件", {p0: item.media_count}) : '',
                  item.spoiler ? tr("剧透") : '',
                ].filter(Boolean).join(' · ')}
              </div>
              <div className="card__row-foot">
                <StatusBadge tone={TONES[item.status] ?? 'neutral'}>
                  {LABELS()[item.status] || item.status}
                </StatusBadge>
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
          {tr("加载更多")}</Button>
      )}
    </div>
  );
}
