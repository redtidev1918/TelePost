import { tr } from "../../lib/i18n";
import { useTranslation } from 'react-i18next';
import { useState } from 'react';
import { Button, Spinner } from '@telegram-apps/telegram-ui';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useParams } from 'react-router-dom';
import { useBotNavigate } from '../../lib/useBotNavigate';
import { fetchMySubmission, LogicalSubmissionDetail, resubmitSubmission } from '../../api/me';
import { fetchEditorialHistory } from '../../api/reviews';
import { SubmissionMedia } from '../../components/SubmissionMedia';
import { useBackButton } from '../../lib/useBackButton';
import { PageHeader } from '../../components/ui/PageHeader';
import { PageSection } from '../../components/ui/PageSection';
import { StatusBadge, StatusTone } from '../../components/ui/StatusBadge';
import { ActionBar } from '../../components/ui/ActionBar';
import { EmptyState } from '../../components/ui/EmptyState';

/**
 * Owner-scoped detail (§44): uses the user-safe /me/submissions/{id} DTO, so a
 * plain submitter never needs reviewer privileges and never sees internal
 * lineage/audit fields.
 *
 * It is a detail route: the ONLY back affordance is the Telegram BackButton
 * (§back) — the page must not repeat back in the header, the body and the
 * bottom of the page at the same time.
 */

const STATUS_LABELS = (): Record<string, string> => ({
  preparing: tr("准备中"),
  in_review: tr("审核中"),
  publishing: tr("发布中"),
  published: tr("已发布"),
  rejected: tr("未通过"),
  failed: tr("处理失败"),
  expired: tr("已过期"),
});

const STATUS_TONES: Record<string, StatusTone> = {
  published: 'good',
  rejected: 'bad',
  failed: 'bad',
  preparing: 'progress',
  in_review: 'progress',
  publishing: 'progress',
};

export function SubmissionDetailPage() {
  useTranslation();
  const { id } = useParams<{ id: string }>();
  const navigate = useBotNavigate();
  useBackButton('/mine');
  const qc = useQueryClient();
  const [resubmitResult, setResubmitResult] = useState<string | null>(null);
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
      setResubmitResult((error as Error).message || tr("重新提交失败，请稍后重试。"));
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
    return (
      <div className="page-loading">
        <Spinner size="m" />
      </div>
    );
  }
  if (submission.isError || !submission.data) {
    return (
      <div className="stack">
        <PageHeader title={tr("投稿详情")} />
        <EmptyState
          title={submission.isError ? (submission.error as Error).message : tr("未找到该投稿")}
          hint={tr("它可能已经被清理，或不在当前账号下。")}
        />
      </div>
    );
  }
  const item: LogicalSubmissionDetail = submission.data;
  const edited = history.data?.edited_before_publication;

  return (
    <div className="stack">
      <PageHeader
        title={item.title || tr("未命名投稿")}
        subtitle={tr("{{p0}} 个媒体 · {{p1}} 个文件", {p0: item.media_count, p1: item.document_count})}
      />

      <div className="card">
        <div className="card__row card__row--static">
          <div className="card__row-meta">{tr("状态")}</div>
          <div className="card__row-foot">
            <StatusBadge tone={STATUS_TONES[item.status] ?? 'neutral'}>
              {STATUS_LABELS()[item.status] || item.status}
            </StatusBadge>
          </div>
        </div>
        {item.note && (
          <div className="card__row card__row--static">
            <div className="card__row-meta">{tr("备注")}</div>
            <div className="card__row-title">{item.note}</div>
          </div>
        )}
        {item.tags.length > 0 && (
          <div className="card__row card__row--static">
            <div className="card__row-meta">{tr("标签")}</div>
            <div className="tag-list">{item.tags.map((tag) => <span key={tag} className="tag">{tag}</span>)}</div>
          </div>
        )}
        {item.link && /^https?:\/\//i.test(item.link) && (
          <div className="card__row card__row--static">
            <div className="card__row-meta">{tr("链接")}</div>
            <a href={item.link} target="_blank" rel="noopener noreferrer" style={{ overflowWrap: 'anywhere' }}>
              {item.link}
            </a>
          </div>
        )}
        <div className="card__row card__row--static">
          <div className="card__row-meta">{tr("剧透")}</div>
          <div className="card__row-foot">
            <StatusBadge tone={item.spoiler ? 'warn' : 'neutral'}>
              {item.spoiler ? tr("含剧透") : tr("无剧透")}
            </StatusBadge>
          </div>
        </div>
        <div className="card__row card__row--static">
          <div className="card__row-meta">{tr("投稿时间")}</div>
          <div className="card__row-title">{new Date(item.created_at * 1000).toLocaleString()}</div>
        </div>
        <div className="card__row card__row--static">
          <div className="card__row-meta">{tr("更新时间")}</div>
          <div className="card__row-title">{new Date(item.updated_at * 1000).toLocaleString()}</div>
        </div>
        {item.refetch_count > 0 && (
          <div className="card__row card__row--static">
            <div className="card__row-meta">{tr("重抓记录")}</div>
            <div className="card__row-title">{tr("已更换候选")}{item.refetch_count} {tr(" 次")}</div>
          </div>
        )}
        {history.data && (history.data.status === 'published' || history.data.revisions.length > 0) && (
          <div className="card__row card__row--static">
            <div className="card__row-meta">{tr("发布前经过编辑")}</div>
            <div className="card__row-title" data-testid="published-edited-flag">
              {edited ? tr("是（可查看修改详情）") : tr("否")}
            </div>
          </div>
        )}
      </div>

      {item.media?.map((attachment) => (
        <SubmissionMedia key={attachment.index}
          path={`/me/submissions/${item.current_review_id}/media/${attachment.index}`}
          attachment={attachment} />
      ))}

      {item.resubmit_available && (
        <ActionBar
          primary={
            <Button
              size="l"
              stretched
              disabled={resubmit.isPending}
              data-testid="resubmit-button"
              onClick={() => resubmit.mutate()}
            >
              {resubmit.isPending ? tr("重投中…") : tr("重投")}
            </Button>
          }
          note={resubmitResult ?? undefined}
        />
      )}
      {resubmitResult && !item.resubmit_available && (
        <div className="card">
          <div className="card__row card__row--static" data-testid="resubmit-result">
            <div className="card__row-title">{tr("重投结果")}</div>
            <div className="card__row-meta">{resubmitResult}</div>
          </div>
        </div>
      )}

      {history.data && history.data.revisions.length > 0 && (
        <PageSection title={tr("编辑记录")}>
          <Button
            mode="outline"
            stretched
            data-testid="view-editorial-history"
            onClick={() => navigate(`/mine/${id}/editorial`)}
          >
            {tr("查看修改详情")}</Button>
        </PageSection>
      )}
    </div>
  );
}
