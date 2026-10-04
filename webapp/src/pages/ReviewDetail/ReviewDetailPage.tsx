import { useState } from 'react';
import { Button, Chip, Spinner, Textarea } from '@telegram-apps/telegram-ui';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useParams } from 'react-router-dom';
import { useBotNavigate } from '../../lib/useBotNavigate';
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
import { PageHeader } from '../../components/ui/PageHeader';
import { PageSection } from '../../components/ui/PageSection';
import { StatusBadge, StatusTone } from '../../components/ui/StatusBadge';
import { ActionBar } from '../../components/ui/ActionBar';
import { EmptyState } from '../../components/ui/EmptyState';

/**
 * ReviewDetail (§30-§36, §review-detail-ux).
 *
 * One dominant decision (通过并发布), one secondary row, and one destructive
 * area — so the reviewer never faces a five-button toolbox on a phone.
 * Approve is irreversible-ish and high impact, so it is always the single
 * primary action with a warning underneath; reject asks for a reason inside its
 * own section instead of squeezing every button into extra rows.
 *
 * Refetch internals (task id, generation, lineage) are one tap away in a
 * collapsed details panel — the reviewer sees the human stage first.
 */

const STATUS_LABELS: Record<string, string> = {
  pending: '待审核',
  publishing: '发布中',
  published: '已发布',
  failed: '失败（可重试）',
  rejected: '已拒绝',
  expired: '已过期',
  superseded: '已被替换',
};

const STATUS_TONES: Record<string, StatusTone> = {
  pending: 'progress',
  publishing: 'progress',
  published: 'good',
  failed: 'bad',
  rejected: 'bad',
};

const REFETCH_LABELS: Record<string, string> = {
  requested: '正在重抓',
  admitted: '正在重抓',
  running: '正在重抓',
  searching: '正在重抓：搜索候选',
  filtering: '正在重抓：筛选候选',
  candidate_found: '正在重抓：候选已就绪',
  replaced: '已找到新的候选',
  no_alternative: '没有新的可替换作品',
  no_candidate: '没有新的可替换作品',
  timeout: '重抓超时（当前稿件不变）',
  cancelled: '当前稿件已过期',
  failed: '重抓失败',
  obsolete: '当前稿件已过期',
};

/** Canonical + legacy active states: the button must stay disabled while the
 * job runs, whichever vocabulary the server reports (§refetch-lifecycle). */
const ACTIVE_REFETCH_STATES = new Set([
  'requested',
  'admitted',
  'searching',
  'filtering',
  'candidate_found',
]);

/** Elapsed wait, rendered the same way the review card renders it. */
function formatElapsed(seconds: number): string {
  const total = Math.max(0, Math.floor(seconds));
  const minutes = Math.floor(total / 60);
  if (minutes < 1) return `${total} 秒`;
  return `${minutes} 分 ${total % 60} 秒`;
}

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
      <div className="stack">
        <PageHeader title="审核" />
        <EmptyState
          title={review.isError ? errorMessage(review.error) : '未找到该审核'}
          hint="它可能已经被处理，或不在当前 bot 的范围内。"
        />
      </div>
    );
  }
  const item = review.data;
  const attempt: RefetchAttempt | null | undefined = refetchState.data?.attempt;
  // Canonical vocabulary when the server reports it, legacy otherwise, so an old
  // bot/AI client keeps working (§refetch-lifecycle, to_legacy on the wire).
  const refetchStateName = attempt ? (attempt.canonical_state || attempt.state) : '';
  const refetchActive = ACTIVE_REFETCH_STATES.has(refetchStateName);
  const refetchFailed = refetchStateName === 'failed' || refetchStateName === 'timeout';
  const refetchLabel =
    attempt?.label || REFETCH_LABELS[refetchStateName] || refetchStateName;
  const actionable = item.status === 'pending' || item.status === 'failed';

  return (
    <div className="stack">
      <PageHeader
        title={item.title || '（无标题）'}
        subtitle={`审核 #${item.id}`}
      />

      <div className="card">
        <div className="card__row card__row--static">
          <div className="card__row-meta">状态</div>
          <div className="card__row-foot">
            <StatusBadge tone={STATUS_TONES[item.status] ?? 'neutral'}>
              {STATUS_LABELS[item.status] || item.status}
            </StatusBadge>
          </div>
        </div>
        {item.tags.length > 0 && (
          <div className="card__row card__row--static">
            <div className="tag-list" style={{ marginTop: 0 }}>
              {item.tags.map((tag) => (
                <Chip key={tag}>{tag}</Chip>
              ))}
            </div>
          </div>
        )}
        {item.note && (
          <div className="card__row card__row--static">
            <div className="card__row-meta">备注</div>
            <div className="card__row-title">{item.note}</div>
          </div>
        )}
        {item.link && (
          <div className="card__row card__row--static">
            <div className="card__row-meta">来源链接</div>
            <a href={item.link} target="_blank" rel="noopener noreferrer" style={{ overflowWrap: 'anywhere' }}>
              {item.link}
            </a>
          </div>
        )}
        {item.source_label && (
          <div className="card__row card__row--static">
            <div className="card__row-meta">来源</div>
            <div className="card__row-title">{item.source_label}</div>
          </div>
        )}
        <div className="card__row card__row--static">
          <div className="card__row-meta">剧透</div>
          <div className="card__row-foot">
            <StatusBadge tone={item.spoiler ? 'warn' : 'neutral'}>
              {item.spoiler ? '开启' : '关闭'}
            </StatusBadge>
          </div>
        </div>
      </div>

      {item.media.map((attachment) => (
        <SubmissionMedia key={attachment.index}
          path={`/reviews/${item.id}/media/${attachment.index}`} attachment={attachment} />
      ))}

      {/* Refetch state (§35-§36, §refetch-lifecycle): the human stage first. */}
      {refetchState.data && attempt && (
        <PageSection title="重抓">
          <div className="card">
            <div className="card__row card__row--static">
              <div className="card__row-title">{refetchLabel}</div>
              {attempt.progress && refetchActive && (
                <div className="card__row-meta">
                  已等待 {formatElapsed(attempt.progress.elapsed_seconds)}
                </div>
              )}
              {refetchFailed && attempt.failure_code && (
                <div className="card__row-meta">{attempt.failure_code}</div>
              )}
              {attempt.terminal_reason && (
                <div className="card__row-meta">{attempt.terminal_reason}</div>
              )}
            </div>
            <details className="details-panel">
              <summary />
              <div style={{ padding: '0 14px 12px' }}>
                {(attempt.progress?.task_id || attempt.task_id) && (
                  <>
                    <div className="card__row-meta">任务ID</div>
                    <div className="card__row-meta">
                      {attempt.progress?.task_id || attempt.task_id}
                    </div>
                  </>
                )}
                <div className="card__row-meta">Generation</div>
                <div className="card__row-meta">{attempt.generation}</div>
                {refetchState.data.lineage.length > 1 && (
                  <>
                    <div className="card__row-meta">候选历史</div>
                    <div className="card__row-meta">
                      {refetchState.data.lineage
                        .map((l) => `G${l.generation}:${l.candidate_id}`)
                        .join(' → ')}
                    </div>
                  </>
                )}
              </div>
            </details>
          </div>
        </PageSection>
      )}

      {mutationError && <div className="error-box">{mutationError}</div>}

      {/* Reviewer mutations (§30-§35) — all server-side RBAC, no blind retry */}
      {actionable && (
        <>
          <ActionBar
            primary={
              <Button
                size="l"
                stretched
                loading={approve.isPending}
                disabled={approve.isPending || refetch.isPending}
                data-testid="approve"
                onClick={() => void approve.mutateAsync()}
              >
                通过并发布
              </Button>
            }
            secondary={
              <>
                <Button
                  size="m"
                  mode="bezeled"
                  data-testid="edit-before-publish"
                  onClick={() => navigate(`/review/${item.id}/edit`)}
                >
                  编辑后发布
                </Button>
                <Button
                  size="m"
                  mode="bezeled"
                  loading={refetch.isPending}
                  disabled={refetch.isPending || refetchState.isPending || refetchState.isError || refetchActive}
                  onClick={() => void refetch.mutateAsync()}
                >
                  重抓
                </Button>
                <Button
                  size="m"
                  mode="bezeled"
                  disabled={spoiler.isPending || item.spoiler}
                  onClick={() => void spoiler.mutateAsync(true)}
                >
                  剧透
                </Button>
              </>
            }
            note="通过/拒绝后不可撤销；并发操作以服务端先到者为准。"
          />

          <PageSection title="不通过">
            {rejectOpen ? (
              <div className="stack">
                <Textarea
                  placeholder="拒绝原因（可选，仅审核记录）"
                  value={rejectReason}
                  onChange={(e) => setRejectReason(e.target.value)}
                />
                <ActionBar
                  primary={
                    <Button
                      mode="outline"
                      stretched
                      loading={reject.isPending}
                      disabled={reject.isPending}
                      style={{ color: 'var(--tgui--destructive_text_color)' }}
                      data-testid="reject-confirm"
                      onClick={() => void reject.mutateAsync()}
                    >
                      确认拒绝
                    </Button>
                  }
                  secondary={
                    <Button
                      mode="plain"
                      stretched
                      disabled={reject.isPending}
                      data-testid="reject-cancel"
                      onClick={() => setRejectOpen(false)}
                    >
                      取消
                    </Button>
                  }
                />
              </div>
            ) : (
              <Button
                mode="outline"
                stretched
                disabled={reject.isPending}
                style={{ color: 'var(--tgui--destructive_text_color)' }}
                data-testid="reject"
                onClick={() => setRejectOpen(true)}
              >
                拒绝
              </Button>
            )}
          </PageSection>
        </>
      )}
    </div>
  );
}
