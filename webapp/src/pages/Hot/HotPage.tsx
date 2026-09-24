import { Section, Spinner, Cell } from '@telegram-apps/telegram-ui';
import { useInfiniteQuery } from '@tanstack/react-query';
import { fetchHotPosts, HotScope } from '../../api/posts';
import { useBotNavigate } from '../../lib/useBotNavigate';
import { PostCard } from '../../components/PostCard';

export function HotPage({ scope }: { scope: HotScope }) {
  const navigate = useBotNavigate();
  const query = useInfiniteQuery({
    queryKey: ['hot-posts', scope],
    queryFn: ({ pageParam }) => fetchHotPosts(scope, pageParam),
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
    return <div className="page-error">加载失败：{(query.error as Error).message}</div>;
  }

  const items = query.data?.pages.flatMap((page) => page.items) ?? [];
  return (
    <Section header={scope === 'week' ? '本周热门' : '全部热门'}>
      {items.length === 0 && (
        <div className="page-empty">还没有热门内容。</div>
      )}
      {items.map((post) => (
        <PostCard
          key={post.message_id}
          post={post}
          onClick={() => navigate(`/post/${post.message_id}`)}
        />
      ))}
      {query.hasNextPage && (
        <Cell data-testid="load-more" onClick={() => void query.fetchNextPage()} after="↓">
          加载更多
        </Cell>
      )}
    </Section>
  );
}
