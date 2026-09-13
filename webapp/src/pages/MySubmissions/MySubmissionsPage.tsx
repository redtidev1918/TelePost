import { Cell, Section, Spinner } from '@telegram-apps/telegram-ui';
import { useInfiniteQuery } from '@tanstack/react-query';
import { useNavigate } from 'react-router-dom';
import { fetchMySubmissions, OwnSubmission } from '../../api/me';

const STATUS_LABELS: Record<string, string> = {
  pending_review: '待审核',
  pending: '待审核',
  publishing: '发布中',
  published: '已发布',
  failed: '失败',
  rejected: '已拒绝',
  expired: '已过期',
  superseded: '已被替换',
  preparing: '准备中',
};

function Status({ status }: { status: string }) {
  const label = STATUS_LABELS[status] || status;
  return <span>{label}</span>;
}

export function MySubmissionsPage() {
  const navigate = useNavigate();
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
  const items: OwnSubmission[] =
    query.data?.pages.flatMap((p) => p.items) ?? [];
  if (items.length === 0) {
    return <div className="page-empty">还没有投稿，去「投稿」页发一条吧。</div>;
  }

  return (
    <Section header="我的投稿">
      {items.map((item) => (
        <Cell
          key={item.review_id}
          onClick={() => navigate(`/mine/${item.review_id}`)}
          after={item.media_count ? `📎 ${item.media_count}` : undefined}
          subtitle={
            <Status status={item.status} />
          }
        >
          {item.title || `投稿 #${item.review_id}`}
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
