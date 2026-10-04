import { formatHeat, formatPublishedAt, PostSummary } from '../api/posts';
import { MediaThumb } from './ui/MediaThumb';

/**
 * Content card (§ui-kit): media-first browsing instead of an admin-style cell.
 *
 * `layout="grid"` is the two-column browsing surface (Hot); `layout="row"` is
 * the compact preview used inside dashboards (Home). Both keep the title,
 * tags and heat readable, and both degrade to a neutral placeholder when the
 * preview cannot be loaded — the text never depends on the image.
 */
export function PostCard({
  post,
  onClick,
  testId = 'post-card',
  layout = 'grid',
  showMedia = true,
}: {
  post: PostSummary;
  onClick?: () => void;
  testId?: string;
  layout?: 'grid' | 'row';
  showMedia?: boolean;
}) {
  const time = formatPublishedAt(post.publish_time);
  const meta = [
    `🔥 ${formatHeat(post.heat_score)}`,
    post.reactions > 0 ? `❤️ ${post.reactions}` : '',
    time,
  ].filter(Boolean).join(' · ');
  const title = post.title || '无标题';
  const hasMedia = showMedia && post.media_count > 0;

  if (layout === 'row') {
    return (
      <button type="button" className="media-row" data-testid={testId} onClick={onClick}>
        {hasMedia ? (
          <MediaThumb
            messageId={post.message_id}
            className="media-row__media"
            imgClassName="media-row__img"
            alt={title}
            enabled
          />
        ) : (
          <div className="media-row__media media-row__media--empty" aria-hidden />
        )}
        <span className="media-row__body">
          <span className="media-row__title">{title}</span>
          <span className="media-row__meta">
            {post.tags.slice(0, 3).map((tag) => `#${tag}`).join(' ')}
            {post.tags.length > 0 ? ' · ' : ''}
            {meta}
          </span>
        </span>
      </button>
    );
  }

  return (
    <button type="button" className="media-card" data-testid={testId} onClick={onClick}>
      {hasMedia ? (
        <MediaThumb messageId={post.message_id} alt={title} enabled />
      ) : (
        <div className="media-card__media media-card__media--empty" aria-hidden />
      )}
      <span className="media-card__body">
        <span className="media-card__title">{title}</span>
        <span className="media-card__meta">
          {post.tags.slice(0, 3).map((tag) => `#${tag}`).join(' ')}
          {post.tags.length > 0 ? ' · ' : ''}
          {meta}
        </span>
      </span>
    </button>
  );
}
