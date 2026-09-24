import { Cell } from '@telegram-apps/telegram-ui';
import { PostSummary, formatHeat, formatPublishedAt } from '../api/posts';

export function PostCard({
  post,
  onClick,
  testId = 'post-card',
}: {
  post: PostSummary;
  onClick?: () => void;
  testId?: string;
}) {
  const time = formatPublishedAt(post.publish_time);
  const subtitle = [
    post.tags.slice(0, 4).map((tag) => `#${tag}`).join(' '),
    `🔥 ${formatHeat(post.heat_score)}`,
    post.reactions > 0 ? `❤️ ${post.reactions}` : '',
    time,
  ].filter(Boolean).join(' · ');

  return (
    <Cell
      data-testid={testId}
      onClick={onClick}
      after="→"
      subtitle={<div className="mine-meta">{subtitle}</div>}
    >
      <div style={{ overflowWrap: 'anywhere' }}>
        {post.title || '无标题'}
      </div>
    </Cell>
  );
}
