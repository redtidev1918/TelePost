import { Button, Cell, Section } from '@telegram-apps/telegram-ui';
import { useQuery } from '@tanstack/react-query';
import { useNavigate, useParams } from 'react-router-dom';
import { fetchMySubmission, LogicalSubmissionDetail } from '../../api/me';
import { useBackButton } from '../../lib/useBackButton';

const STATUS_LABELS: Record<string, string> = {
  preparing: '准备中',
  in_review: '审核中',
  publishing: '发布中',
  published: '已发布',
  rejected: '未通过',
  failed: '处理失败',
  expired: '已过期',
};

/**
 * Owner-scoped detail (§44): uses the user-safe /me/submissions/{id} DTO, so a
 * plain submitter never needs reviewer privileges and never sees internal
 * lineage/audit fields.
 */
export function SubmissionDetailPage() {
  const { id } = useParams<{ id: string }>();
  const navigate = useNavigate();
  useBackButton('/mine');
  const submission = useQuery({
    queryKey: ['my-submission', id],
    queryFn: () => fetchMySubmission(id!),
  });

  if (submission.isLoading) {
    return <div className="page-loading">加载中…</div>;
  }
  if (submission.isError || !submission.data) {
    return (
      <div className="page-error">
        {submission.isError ? (submission.error as Error).message : '未找到'}
        <div style={{ marginTop: 12 }}>
          <Button onClick={() => navigate('/mine')}>返回我的投稿</Button>
        </div>
      </div>
    );
  }
  const item: LogicalSubmissionDetail = submission.data;
  return (
    <div>
      <Section header="投稿详情">
        <Cell subtitle={STATUS_LABELS[item.status] || item.status}>
          {item.title || '未命名投稿'}
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
        <Cell
          subtitle={`${item.media_count} 个媒体 · ${item.document_count} 个文件`}
        >
          {item.spoiler ? '含剧透' : '无剧透'}
        </Cell>
        {item.refetch_count > 0 && (
          <Cell subtitle={`已更换候选 ${item.refetch_count} 次`}>重抓记录</Cell>
        )}
      </Section>
      <div style={{ marginTop: 16 }}>
        <Button mode="bezeled" onClick={() => navigate('/mine')}>
          返回
        </Button>
      </div>
    </div>
  );
}