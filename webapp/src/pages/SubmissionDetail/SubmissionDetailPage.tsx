import React from 'react';
import { useBotNavigate } from '../../lib/useBotNavigate';
import { Button, Cell, Section } from '@telegram-apps/telegram-ui';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useParams } from 'react-router-dom';
import { fetchMySubmission, LogicalSubmissionDetail, resubmitSubmission } from '../../api/me';
import { fetchEditorialHistory } from '../../api/reviews';
import { SubmissionMedia } from '../../components/SubmissionMedia';
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
  const navigate = useBotNavigate();
  useBackButton('/mine');
  const qc = useQueryClient();
  const [resubmitResult, setResubmitResult] = React.useState<string | null>(null);
  const submission = useQuery({
    queryKey: ['my-submission', id],
    queryFn: () => fetchMySubmission(id!),
  });
  const resubmit = useMutation({
    mutationFn: () => resubmitSubmission(id!),
    onSuccess: (result) => {
      setResubmitResult(result.message);
      void qc.invalidateQueries({ queryKey: ['my-submission', id] });
    },
    onError: (error: unknown) => {
      setResubmitResult((error as Error).message || '重新提交失败，请稍后重试。');
    },
  });
  // Publication + editorial context for the owner (§47): the SERVER decides
  // whether publication was preceded by an edit; the UI only renders it.
  const history = useQuery({
    queryKey: ['editorial-history', id],
    queryFn: () => fetchEditorialHistory(id!),
    retry: false,
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
        {item.link && /^https?:\/\//i.test(item.link) && (
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
        <Cell subtitle={new Date(item.created_at * 1000).toLocaleString()}>投稿时间</Cell>
        <Cell subtitle={new Date(item.updated_at * 1000).toLocaleString()}>更新时间</Cell>
        {item.media?.map((attachment) => (
          <SubmissionMedia key={attachment.index}
            path={`/me/submissions/${item.current_review_id}/media/${attachment.index}`}
            attachment={attachment} />
        ))}
        {item.refetch_count > 0 && (
          <Cell subtitle={`已更换候选 ${item.refetch_count} 次`}>重抓记录</Cell>
        )}
        {history.data && (history.data.status === 'published' || history.data.revisions.length > 0) && (
          <Cell
            subtitle={history.data.edited_before_publication ? '是（可查看修改详情）' : '否'}
            data-testid="published-edited-flag"
          >
            发布前经过编辑
          </Cell>
        )}
      </Section>
      {item.resubmit_available && (
        <div style={{ padding: '0 16px 12px' }}>
          <Button
            stretched
            disabled={resubmit.isPending}
            data-testid="resubmit-button"
            onClick={() => resubmit.mutate()}
          >
            {resubmit.isPending ? '重投中…' : '重投'}
          </Button>
          {resubmitResult && (
            <Cell subtitle={resubmitResult} data-testid="resubmit-result">
              重投结果
            </Cell>
          )}
        </div>
      )}
      {history.data && history.data.revisions.length > 0 && (
        <div style={{ padding: '0 16px 12px' }}>
          <Button
            stretched
            mode="bezeled"
            data-testid="view-editorial-history"
            onClick={() => navigate(`/mine/${id}/editorial`)}
          >
            查看修改详情
          </Button>
        </div>
      )}
      <div style={{ marginTop: 16 }}>
        <Button mode="bezeled" onClick={() => navigate('/mine')}>
          返回
        </Button>
      </div>
    </div>
  );
}