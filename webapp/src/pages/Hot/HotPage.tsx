import { Button, Spinner } from '@telegram-apps/telegram-ui';
import { useInfiniteQuery } from '@tanstack/react-query';
import { fetchHotPosts, HotScope } from '../../api/posts';
import { useBotNavigate } from '../../lib/useBotNavigate';
import { PostCard } from '../../components/PostCard';
import { PageHeader } from '../../components/ui/PageHeader';
import { Segmented } from '../../components/ui/Segmented';
import { EmptyState } from '../../components/ui/EmptyState';

/**
 * Hot = content browsing (§hot).
 *
 * A media-first grid instead of a cell list: the thumbnail leads, the title and
 * a single compact metadata line follow, and the whole card is one tap target.
 * Previews are lazy and bounded (MediaThumb), so scrolling never pulls the full
 * media set.
 */
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
    return (
      <div className="stack">
        <PageHeader title={scope === 'week' ? '本周热门' : '全部热门'} />
        <EmptyState title="暂时无法加载热门内容" hint={(query.error as Error).message} />
      </div>
    );
  }

  const items = query.data?.pages.flatMap((page) => page.items) ?? [];
  return (
    <div className="stack">
      <PageHeader title={scope === 'week' ? '本周热门' : '全部热门'} />
      <Segmented<HotScope>
        testId="hot-scope"
        value={scope}
        options={[
          { value: 'all', label: '全部' },
          { value: 'week', label: '本周' },
        ]}
        onChange={(next) => navigate(next === 'week' ? '/hotweek' : '/hot')}
      />
      {items.length === 0 ? (
        <EmptyState title="还没有热门内容" hint="频道里发布的内容会按热度出现在这里。" />
      ) : (
        <div className="media-grid" data-testid="hot-grid">
          {items.map((post) => (
            <PostCard
              key={post.message_id}
              post={post}
              layout="grid"
              onClick={() => navigate(`/post/${post.message_id}`)}
            />
          ))}
        </div>
      )}
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
