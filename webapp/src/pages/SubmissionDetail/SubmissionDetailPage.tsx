import { Button, Cell, Section } from '@telegram-apps/telegram-ui';
import { useQuery } from '@tanstack/react-query';
import { useNavigate, useParams } from 'react-router-dom';
import { fetchReview, ReviewDetail } from '../../api/reviews';
import { useBackButton } from '../../lib/useBackButton';

const STATUS_LABELS: Record<string, string> = {
  pending: '待审核',
  publishing: '发布中',
  published: '已发布',
  failed: '失败',
  rejected: '已拒绝',
  expired: '已过期',
  superseded: '已被替换',
};

export function SubmissionDetailPage() {
  const { id } = useParams<{ id: string }>();
  const navigate = useNavigate();
  useBackButton('/mine');
  const review = useQuery({
    queryKey: ['review', id],
    queryFn: () => fetchReview(id!),
  });

  if (review.isLoading) {
    return <div className="page-loading">加载中…</div>;
  }
  if (review.isError || !review.data) {
    return (
      <div className="page-error">
        {review.isError ? (review.error as Error).message : '未找到'}
        <div style={{ marginTop: 12 }}>
          <Button onClick={() => navigate('/mine')}>返回我的投稿</Button>
        </div>
      </div>
    );
  }
  const item: ReviewDetail = review.data;
  return (
    <div>
      <Section header={`投稿 #${item.id}`}>
        <Cell subtitle={STATUS_LABELS[item.status] || item.status}>
          {item.title || '（无标题）'}
        </Cell>
        {item.note && <Cell subtitle={item.note}>备注</Cell>}
        {item.tags.length > 0 && <Cell subtitle={item.tags.join(' ')}>标签</Cell>}
        {item.link && (
          <Cell
            subtitle={
              <a href={item.link} target="_blank" rel="noopener noreferrer">
                {item.link}
              </a>
            }
          >
            链接
          </Cell>
        )}
        <Cell subtitle={`${item.media.length} 个媒体 / 文件`}>
          {item.spoiler ? '含剧透' : '无剧透'}
        </Cell>
      </Section>
      <div style={{ marginTop: 16 }}>
        <Button mode="bezeled" onClick={() => navigate('/mine')}>
          返回
        </Button>
      </div>
    </div>
  );
}
