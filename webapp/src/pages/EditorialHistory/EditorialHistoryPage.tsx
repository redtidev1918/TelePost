import { Chip, Spinner } from '@telegram-apps/telegram-ui';
import { useQuery } from '@tanstack/react-query';
import { useParams } from 'react-router-dom';
import { fetchEditorialHistory } from '../../api/reviews';
import { ApiError } from '../../api/client';
import { useBackButton } from '../../lib/useBackButton';
import { PageHeader } from '../../components/ui/PageHeader';
import { PageSection } from '../../components/ui/PageSection';
import { StatusBadge } from '../../components/ui/StatusBadge';
import { EmptyState } from '../../components/ui/EmptyState';

/**
 * Submitter-facing editorial history (§48-§50).
 *
 * Shows what was published and what the channel owner changed beforehand.
 * NEVER exposes editor ids, internal moderation fields or refetch metadata:
 * the editor is rendered as a label ('频道管理员').
 *
 * Detail route: back comes from the Telegram BackButton only (§back).
 */
export function EditorialHistoryPage() {
  const { id } = useParams<{ id: string }>();
  useBackButton(`/mine/${id}`);
  const history = useQuery({
    queryKey: ['editorial-history', id],
    queryFn: () => fetchEditorialHistory(id!),
  });

  if (history.isLoading) {
    return (
      <div className="page-loading">
        <Spinner size="m" />
      </div>
    );
  }
  if (history.isError || !history.data) {
    return (
      <div className="stack">
        <PageHeader title="修改详情" />
        <EmptyState
          title={history.error instanceof ApiError ? history.error.message : '未找到该投稿'}
          hint="它可能已经被清理，或没有可展示的编辑记录。"
        />
      </div>
    );
  }
  const data = history.data;

  return (
    <div className="stack">
      <PageHeader title="修改详情" subtitle={`投稿 #${data.review_id}`} />

      <div className="card">
        <div className="card__row card__row--static">
          <div className="card__row-meta">发布状态</div>
          <div className="card__row-title" data-testid="history-status">{data.status}</div>
        </div>
        <div className="card__row card__row--static">
          <div className="card__row-meta">发布前经过编辑</div>
          <div className="card__row-foot">
            <StatusBadge tone={data.edited_before_publication ? 'progress' : 'neutral'}>
              <span data-testid="history-edited">{data.edited_before_publication ? '是' : '否'}</span>
            </StatusBadge>
          </div>
        </div>
      </div>

      {data.revisions.length === 0 && (
        <EmptyState title="本次投稿按原稿发布，没有编辑记录。" />
      )}

      {data.revisions.map((rev) => (
        <PageSection key={rev.revision_number} title={`版本 ${rev.revision_number} · ${rev.editor_display}`}>
          <div className="card">
            <div className="card__row card__row--static">
              <div className="card__row-meta">修改摘要</div>
              <div className="card__row-title" data-testid={`history-summary-${rev.revision_number}`}>
                {rev.summary || '仅调整格式'}
              </div>
              <div className="tag-list">
                {rev.change_set.title && <Chip>标题</Chip>}
                {rev.change_set.note && <Chip>简介</Chip>}
                {!!rev.change_set.tags?.added?.length && <Chip>新增标签</Chip>}
                {!!rev.change_set.tags?.removed?.length && <Chip>移除标签</Chip>}
                {rev.change_set.link && <Chip>链接</Chip>}
                {rev.change_set.spoiler && <Chip>剧透</Chip>}
                {rev.change_set.media?.reordered && <Chip>图片顺序</Chip>}
                {!!rev.change_set.media?.removed?.length && <Chip>移除附件</Chip>}
              </div>
            </div>
            <div className="card__row card__row--static">
              <div className="card__row-meta">发布标题</div>
              <div className="card__row-title">{rev.published_snapshot.title || '（无标题）'}</div>
            </div>
            {!!rev.published_snapshot.tags && (
              <div className="card__row card__row--static">
                <div className="card__row-meta">发布标签</div>
                <div className="card__row-title">{rev.published_snapshot.tags}</div>
              </div>
            )}
            {!!rev.published_snapshot.note && (
              <div className="card__row card__row--static">
                <div className="card__row-meta">发布简介</div>
                <div className="card__row-title">{rev.published_snapshot.note}</div>
              </div>
            )}
            {rev.change_set.title && (
              <div className="card__row card__row--static">
                <div className="card__row-meta">标题对比</div>
                <div className="card__row-title">原稿：{rev.change_set.title.before}</div>
              </div>
            )}
            {(rev.change_set.tags?.added?.length || rev.change_set.tags?.removed?.length) && (
              <div className="card__row card__row--static">
                <div className="card__row-meta">标签变化</div>
                <div className="card__row-title">
                  {[
                    ...(rev.change_set.tags?.added || []).map((t) => `+${t}`),
                    ...(rev.change_set.tags?.removed || []).map((t) => `-${t}`),
                  ].join(' ')}
                </div>
              </div>
            )}
            {!!rev.change_set.media?.removed?.length && (
              <div className="card__row card__row--static">
                <div className="card__row-meta">附件变化</div>
                <div className="card__row-title">
                  移除 {rev.change_set.media.removed.length} 个附件
                </div>
              </div>
            )}
          </div>
        </PageSection>
      ))}
      <div className="mutation-help">
        编辑记录由频道管理员操作产生，原始投稿始终保留。
      </div>
    </div>
  );
}
