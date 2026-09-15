import { useBotNavigate } from '../../lib/useBotNavigate';
import { Cell, Section, Spinner, Chip, Button } from '@telegram-apps/telegram-ui';
import { useQuery } from '@tanstack/react-query';
import { useParams } from 'react-router-dom';
import { fetchEditorialHistory } from '../../api/reviews';
import { ApiError } from '../../api/client';
import { useBackButton } from '../../lib/useBackButton';

/**
 * Submitter-facing editorial history (§48-§50).
 *
 * Shows what was published and what the channel owner changed beforehand.
 * NEVER exposes editor ids, internal moderation fields or refetch metadata:
 * the editor is rendered as a label ('频道管理员').
 */
export function EditorialHistoryPage() {
  const { id } = useParams<{ id: string }>();
  const navigate = useBotNavigate();
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
      <div className="page-error">
        {history.error instanceof ApiError ? history.error.message : '未找到该投稿'}
        <div style={{ marginTop: 12 }}>
          <Button onClick={() => navigate('/mine')}>返回我的投稿</Button>
        </div>
      </div>
    );
  }
  const data = history.data;

  return (
    <div>
      <Section header={`投稿 #${data.review_id} 的发布记录`}>
        <Cell subtitle={data.status} data-testid="history-status">发布状态</Cell>
        <Cell subtitle={data.edited_before_publication ? '是' : '否'} data-testid="history-edited">
          发布前经过编辑
        </Cell>
      </Section>

      {data.revisions.length === 0 && (
        <div className="mutation-help" style={{ padding: '0 16px 12px' }}>
          本次投稿按原稿发布，没有编辑记录。
        </div>
      )}

      {data.revisions.map((rev) => (
        <Section key={rev.revision_number} header={`版本 ${rev.revision_number} · ${rev.editor_display}`}>
          <Cell subtitle={rev.summary || '仅调整格式'} data-testid={`history-summary-${rev.revision_number}`}>
            修改摘要
          </Cell>
          <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap', padding: '0 16px 8px' }}>
            {rev.change_set.title && <Chip>标题</Chip>}
            {rev.change_set.note && <Chip>简介</Chip>}
            {!!rev.change_set.tags?.added?.length && <Chip>新增标签</Chip>}
            {!!rev.change_set.tags?.removed?.length && <Chip>移除标签</Chip>}
            {rev.change_set.link && <Chip>链接</Chip>}
            {rev.change_set.spoiler && <Chip>剧透</Chip>}
            {rev.change_set.media?.reordered && <Chip>图片顺序</Chip>}
            {!!rev.change_set.media?.removed?.length && <Chip>移除附件</Chip>}
          </div>

          <Cell subtitle={rev.published_snapshot.title || '（无标题）'}>发布标题</Cell>
          {!!rev.published_snapshot.tags && (
            <Cell subtitle={rev.published_snapshot.tags}>发布标签</Cell>
          )}
          {!!rev.published_snapshot.note && (
            <Cell subtitle={rev.published_snapshot.note}>发布简介</Cell>
          )}
          {rev.change_set.title && (
            <Cell subtitle={`原稿：${rev.change_set.title.before}`}>标题对比</Cell>
          )}
          {(rev.change_set.tags?.added?.length || rev.change_set.tags?.removed?.length) && (
            <Cell
              subtitle={[
                ...(rev.change_set.tags?.added || []).map((t) => `+${t}`),
                ...(rev.change_set.tags?.removed || []).map((t) => `-${t}`),
              ].join(' ')}
            >
              标签变化
            </Cell>
          )}
          {!!rev.change_set.media?.removed?.length && (
            <Cell subtitle={`移除 ${rev.change_set.media.removed.length} 个附件`}>附件变化</Cell>
          )}
        </Section>
      ))}
    </div>
  );
}