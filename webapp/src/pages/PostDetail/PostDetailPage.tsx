import { Button, Spinner } from '@telegram-apps/telegram-ui';
import { useQuery } from '@tanstack/react-query';
import { useParams } from 'react-router-dom';
import { fetchPost, formatHeat, formatPublishedAt } from '../../api/posts';
import { MediaThumb } from '../../components/ui/MediaThumb';
import { PageHeader } from '../../components/ui/PageHeader';
import { EmptyState } from '../../components/ui/EmptyState';
import { useBackButton } from '../../lib/useBackButton';

/**
 * PostDetail = a real content page (§hot).
 *
 * media → title → metadata → tags → note → source → channel entry, in that
 * order. It is a detail route, so its only back affordance is the Telegram
 * BackButton (§back) — no in-page back button.
 */
export function PostDetailPage() {
  const params = useParams();
  const messageId = params.id ?? '';
  useBackButton('/hot');
  const query = useQuery({
    queryKey: ['post', messageId],
    queryFn: () => fetchPost(messageId),
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
        <PageHeader title="内容" />
        <EmptyState title="暂时无法加载这条内容" hint={(query.error as Error).message} />
      </div>
    );
  }
  const post = query.data;
  if (!post) {
    return (
      <div className="stack">
        <PageHeader title="内容" />
        <EmptyState title="帖子不存在" />
      </div>
    );
  }

  const time = formatPublishedAt(post.publish_time);

  return (
    <div className="stack">
      <PageHeader
        title={post.title || '无标题'}
        subtitle={[
          `🔥 ${formatHeat(post.heat_score)}`,
          post.reactions > 0 ? `❤️ ${post.reactions}` : '',
          time,
        ].filter(Boolean).join(' · ')}
      />
      {post.media_count > 0 && (
        <div className="post-detail__media">
          <MediaThumb
            messageId={messageId}
            testId="post-media"
            className="post-detail__frame"
            imgClassName="post-detail__img"
            alt={post.title || ''}
            enabled
          />
        </div>
      )}
      {post.tags.length > 0 && (
        <div className="tag-list">
          {post.tags.map((tag) => (
            <span key={tag} className="tag" data-testid="post-tag">
              #{tag}
            </span>
          ))}
        </div>
      )}
      {post.note ? <p className="post-detail__note">{post.note}</p> : null}
      {post.link && (
        <Button
          mode="outline"
          stretched
          data-testid="post-link"
          onClick={() => window.open(post.link, '_blank', 'noopener')}
        >
          打开来源
        </Button>
      )}
      <div className="mutation-help">完整内容在 Telegram 频道中查看。</div>
    </div>
  );
}
