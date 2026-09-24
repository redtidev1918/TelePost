import { useBotNavigate } from '../../lib/useBotNavigate';
import { Cell, Section, Spinner } from '@telegram-apps/telegram-ui';
import { useQuery } from '@tanstack/react-query';
import { useAuth } from '../../auth/AuthProvider';
import { fetchMe } from '../../api/me';
import { fetchHotPosts } from '../../api/posts';
import { PostCard } from '../../components/PostCard';

const ROLE_LABELS: Record<string, string> = {
  submitter: '投稿者',
  reviewer: '审核员',
  admin: '管理员',
};

function HotSection({
  scope,
  header,
}: {
  scope: 'all' | 'week';
  header: string;
}) {
  const navigate = useBotNavigate();
  const query = useQuery({
    queryKey: ['hot-preview', scope],
    queryFn: () => fetchHotPosts(scope),
  });

  if (query.isLoading) {
    return <div className="page-loading"><Spinner size="s" /></div>;
  }
  if (query.isError) return null;
  const items = (query.data?.items ?? []).slice(0, 3);
  return (
    <Section header={header}>
      {items.length === 0 && (
        <div className="page-empty">暂无内容。</div>
      )}
      {items.map((post) => (
        <PostCard
          key={post.message_id}
          post={post}
          onClick={() => navigate(`/post/${post.message_id}`)}
        />
      ))}
      <Cell data-testid={`view-${scope}`} onClick={() => navigate(scope === 'week' ? '/hotweek' : '/hot')} after="→">
        查看全部
      </Cell>
    </Section>
  );
}

export function HomePage() {
  const { user, isReviewer } = useAuth();
  const navigate = useBotNavigate();
  const me = useQuery({ queryKey: ['me'], queryFn: fetchMe });

  return (
    <div>
      <Section header="TelePost">
        {me.isLoading ? (
          <div className="page-loading">
            <Spinner size="m" />
          </div>
        ) : (
          <Cell subtitle={
            (me.data?.name || user?.username || `用户 ${user?.telegram_user_id || ''}`) +
            (user?.roles?.length
              ? ` · ${user.roles.map((r) => ROLE_LABELS[r] || r).join(' / ')}`
              : '')
          }>
            {me.data ? `#${me.data.telegram_user_id}` : 'TelePost'}
          </Cell>
        )}
      </Section>

      <HotSection scope="all" header="热门内容" />
      <HotSection scope="week" header="本周热门" />

      <Section header="创作">
        <Cell onClick={() => navigate('/submit')} after="→">
          投稿
        </Cell>
        <Cell onClick={() => navigate('/mine')} after="→">
          我的投稿
        </Cell>
        {isReviewer && (
          <Cell onClick={() => navigate('/review')} after="→">
            审核队列
          </Cell>
        )}
      </Section>
    </div>
  );
}
