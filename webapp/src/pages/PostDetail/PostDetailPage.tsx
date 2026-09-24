import { useEffect, useState } from 'react';
import { Button, Cell, Section, Spinner } from '@telegram-apps/telegram-ui';
import { useQuery } from '@tanstack/react-query';
import { useParams } from 'react-router-dom';
import { fetchPost, fetchPostMedia, formatHeat, formatPublishedAt } from '../../api/posts';

function PostMedia({ messageId }: { messageId: string }) {
  const [url, setUrl] = useState<string | null>(null);
  const query = useQuery({
    queryKey: ['post-media', messageId],
    queryFn: ({ signal }) => fetchPostMedia(messageId, 0, 'preview', signal),
    retry: false,
  });
  useEffect(() => {
    if (query.data) {
      const objectUrl = URL.createObjectURL(query.data);
      setUrl(objectUrl);
      return () => URL.revokeObjectURL(objectUrl);
    }
  }, [query.data]);

  if (query.isLoading) return <div className="page-loading"><Spinner size="s" /></div>;
  if (query.isError || !url) return null;
  return (
    <img
      src={url}
      alt=""
      data-testid="post-media"
      style={{ display: 'block', width: '100%', borderRadius: 12, marginBottom: 8 }}
    />
  );
}

export function PostDetailPage() {
  const params = useParams();
  const messageId = params.id ?? '';
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
    return <div className="page-error">加载失败：{(query.error as Error).message}</div>;
  }
  const post = query.data;
  if (!post) return <div className="page-empty">帖子不存在。</div>;

  return (
    <Section header={post.title || '无标题'}>
      <div style={{ padding: '12px 16px 4px' }}>
        {post.media_count > 0 && <PostMedia messageId={messageId} />}
        <div className="mine-meta">
          {`🔥 ${formatHeat(post.heat_score)}`}
          {post.reactions > 0 && ` · ❤️ ${post.reactions}`}
          {formatPublishedAt(post.publish_time) && ` · ${formatPublishedAt(post.publish_time)}`}
        </div>
        {post.tags.length > 0 && (
          <div style={{ marginTop: 8, display: 'flex', flexWrap: 'wrap', gap: 8 }}>
            {post.tags.map((tag) => (
              <span key={tag} data-testid="post-tag">#{tag}</span>
            ))}
          </div>
        )}
        {post.note && <p style={{ lineHeight: 1.5, whiteSpace: 'pre-wrap' }}>{post.note}</p>}
        {post.link && (
          <Button
            data-testid="post-link"
            mode="outline"
            onClick={() => window.open(post.link, '_blank', 'noopener')}
          >
            打开来源
          </Button>
        )}
      </div>
      <Cell subtitle="在 Telegram 中查看完整频道帖">频道内容</Cell>
    </Section>
  );
}
