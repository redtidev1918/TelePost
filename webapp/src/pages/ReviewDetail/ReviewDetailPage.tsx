import { useBotNavigate } from '../../lib/useBotNavigate';
import { useState } from 'react';
import {
  Button,
  Cell,
  Chip,
  Section,
  Spinner,
  Textarea,
} from '@telegram-apps/telegram-ui';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useParams } from 'react-router-dom';
import {
  approveReview,
  fetchRefetchAttempt,
  fetchReview,
  rejectReview,
  requestRefetch,
  setSpoiler,
  RefetchAttempt,
} from '../../api/reviews';
import { SubmissionMedia } from '../../components/SubmissionMedia';
import { ApiError } from '../../api/client';
import { useBackButton } from '../../lib/useBackButton';

const STATUS_LABELS: Record<string, string> = {
  pending: '待审核',
  publishing: '发布中',
  published: '已发布',
  failed: '失败（可重试）',
  rejected: '已拒绝',
  expired: '已过期',
  superseded: '已被替换',
};

const REFETCH_LABELS: Record<string, string> = {
  requested: '正在重抓',
  admitted: '正在重抓',
  running: '正在重抓',
  replaced: '已找到新的候选',
  no_alternative: '没有新的可替换作品',
  failed: '重抓失败',
  obsolete: '当前稿件已过期',
};

export function ReviewDetailPage() {
  const { id } = useParams<{ id: string }>();
  const navigate = useBotNavigate();
  const queryClient = useQueryClient();
  useBackButton('/review');
  const [rejectOpen, setRejectOpen] = useState(false);
  const [rejectReason, setRejectReason] = useState('');

  const review = useQuery({
    queryKey: ['review', id],
    queryFn: () => fetchReview(id!),
    refetchInterval: 15_000,
  });
  const refetchState = useQuery({
    queryKey: ['review-refetch', id],
    queryFn: () => fetchRefetchAttempt(id!),
    refetchInterval: 8_000,
    refetchIntervalInBackground: true,
  });

  const invalidate = () => {
    void queryClient.invalidateQueries({ queryKey: ['review', id] });
    void queryClient.invalidateQueries({ queryKey: ['review-refetch', id] });
    void queryClient.invalidateQueries({ queryKey: ['review-queue'] });
  };

  const approve = useMutation({
    mutationFn: () => approveReview(id!),
    onSuccess: () => {
      invalidate();
      window.Telegram?.WebApp?.HapticFeedback?.notificationOccurred('success');
    },
    onError: () => {
      window.Telegram?.WebApp?.HapticFeedback?.notificationOccurred('error');
    },
  });
  const reject = useMutation({
    mutationFn: () => rejectReview(id!, rejectReason.trim() || undefined),
    onSuccess: () => {
      invalidate();
      setRejectOpen(false);
    },
    onError: () => undefined,
  });
  const spoiler = useMutation({
    mutationFn: (value: boolean) => setSpoiler(id!, value),
    onSuccess: invalidate,
  });
  const refetch = useMutation({
    mutationFn: () => requestRefetch(id!),
    onSuccess: () => {
      invalidate();
      void queryClient.invalidateQueries({ queryKey: ['review-refetch', id] });
    },
    onError: () => undefined,
  });

  const errorMessage = (error: unknown) =>
    error instanceof ApiError ? error.message : (error as Error).message;
  const mutationError =
    (approve.isError && errorMessage(approve.error)) ||
    (reject.isError && errorMessage(reject.error)) ||
    (refetch.isError && errorMessage(refetch.error)) ||
    null;

  if (review.isLoading) {
    return (
      <div className="page-loading">
        <Spinner size="m" />
      </div>
    );
  }
  if (review.isError || !review.data) {
    return (
      <div className="page-error">
        {review.isError ? errorMessage(review.error) : '未找到'}
        <div style={{ marginTop: 12 }}>
          <Button onClick={() => navigate('/review')}>返回审核队列</Button>
        </div>
      </div>
    );
  }
  const item = review.data;
  const attempt: RefetchAttempt | null | undefined = refetchState.data?.attempt;

  return (
    <div>
      <Section header={`审核 #${item.id}`}>
        <Cell subtitle={STATUS_LABELS[item.status] || item.status}>
          {item.title || '（无标题）'}
        </Cell>
        {item.media.map((attachment) => (
          <SubmissionMedia key={attachment.index}
            path={`/reviews/${item.id}/media/${attachment.index}`} attachment={attachment} />
        ))}
        {item.tags.length > 0 && (
          <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap', padding: '0 16px 12px' }}>
            {item.tags.map((tag) => (
              <Chip key={tag}>{tag}</Chip>
            ))}
          </div>
        )}
        {item.note && <Cell subtitle={item.note}>备注</Cell>}
        {item.link && (
          <Cell
            subtitle={
              <a href={item.link} target="_blank" rel="noopener noreferrer">
                {item.link}
              </a>
            }
          >
            来源链接
          </Cell>
        )}
        {item.source_label && <Cell subtitle={item.source_label}>来源</Cell>}
        <Cell subtitle={item.spoiler ? '开启' : '关闭'}>剧透</Cell>
      </Section>

      {/* Refetch state (§35-§36) */}
      {refetchState.data && attempt && (
        <Section header="重抓">
          <Cell subtitle={REFETCH_LABELS[attempt.state] || attempt.state}>
            Generation {attempt.generation}
          </Cell>
          {attempt.state === 'failed' && attempt.failure_code && (
            <Cell subtitle={attempt.failure_code}>失败原因</Cell>
          )}
          {refetchState.data.lineage.length > 1 && (
            <Cell subtitle={refetchState.data.lineage.map((l) => `G${l.generation}:${l.candidate_id}`).join(' → ')}>
              候选历史
            </Cell>
          )}
        </Section>
      )}

      {mutationError && <div className="error-box">{mutationError}</div>}

      {/* Reviewer mutations (§30-§35) — all server-side RBAC, no blind retry */}
      {(item.status === 'pending' || item.status === 'failed') && (
        <Section header="操作">
          <div style={{ display: 'flex', gap: 8, padding: '0 16px 12px', flexWrap: 'wrap' }}>
            <Button
              size="s"
              loading={approve.isPending}
              disabled={approve.isPending || refetch.isPending}
              onClick={() => void approve.mutateAsync()}
            >
              ✅ 通过并发布
            </Button>
            <Button
              size="s"
              mode="plain"
              style={{ color: "var(--tgui--destructive_text_color)" }}
              loading={reject.isPending}
              disabled={reject.isPending}
              onClick={() => setRejectOpen((v) => !v)}
            >
              ❌ 拒绝
            </Button>
            <Button
              size="s"
              mode="bezeled"
              data-testid="edit-before-publish"
              onClick={() => navigate(`/review/${item.id}/edit`)}
            >
              ✏️ 编辑后发布
            </Button>
            <Button
              size="s"
              mode="bezeled"
              disabled={spoiler.isPending || item.spoiler}
              onClick={() => void spoiler.mutateAsync(true)}
            >
              🫥 设为剧透
            </Button>
            <Button
              size="s"
              mode="bezeled"
              loading={refetch.isPending}
              disabled={refetch.isPending || refetchState.isPending || refetchState.isError || attempt?.state === 'requested' || attempt?.state === 'admitted'}
              onClick={() => void refetch.mutateAsync()}
            >
              🔄 重抓
            </Button>
          </div>
          {rejectOpen && (
            <div style={{ padding: '0 16px 12px' }}>
              <Textarea
                placeholder="拒绝原因（可选，仅审核记录）"
                value={rejectReason}
                onChange={(e) => setRejectReason(e.target.value)}
              />
              <div style={{ marginTop: 8 }}>
                <Button
                  size="s"
                  mode="plain"
                  style={{ color: "var(--tgui--destructive_text_color)" }}
                  loading={reject.isPending}
                  disabled={reject.isPending}
                  onClick={() => void reject.mutateAsync()}
                >
                  确认拒绝
                </Button>
              </div>
            </div>
          )}
        </Section>
      )}
      <div className="mutation-help" style={{ padding: '0 16px 12px' }}>
        通过/拒绝后不可撤销；并发操作以服务端先到者为准（另一边会提示已处理）。
      </div>
    </div>
  );
}
