import { useEffect, useMemo, useState } from 'react';
import {
  Button,
  Chip,
  Input,
  Spinner,
  Switch,
  Textarea,
} from '@telegram-apps/telegram-ui';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useParams } from 'react-router-dom';
import { useBotNavigate } from '../../lib/useBotNavigate';
import {
  EditorialChangeSet,
  EditorialRevision,
  createEditorialRevision,
  fetchEditorialRevisions,
  fetchReview,
  finalizeEditorialRevision,
  previewEditorialRevision,
  publishReview,
  updateEditorialRevision,
} from '../../api/reviews';
import { SubmissionMedia } from '../../components/SubmissionMedia';
import { ApiError } from '../../api/client';
import { useBackButton } from '../../lib/useBackButton';
import { PageHeader } from '../../components/ui/PageHeader';
import { PageSection } from '../../components/ui/PageSection';
import { StatusBadge } from '../../components/ui/StatusBadge';
import { ActionBar } from '../../components/ui/ActionBar';
import { EmptyState } from '../../components/ui/EmptyState';

/**
 * Reviewer "编辑后发布" (§editorial, §review-edit-ux).
 *
 * The reviewer edits a REVISION: the original submission row is never mutated,
 * media reorder/removal only builds a publication subset (originals are kept),
 * edits are server-validated and CAS-protected (a stale save is a 409), and
 * only a FINALIZED revision may be published. The caption preview always comes
 * from the server formatter — never re-implemented here.
 *
 * Presentation: content → media → change summary → pre-publish warning → one
 * action bar. 发布此版本 is the only dominant final action, and it stays
 * disabled until the revision is finalized.
 */
export function ReviewEditPage() {
  const { id } = useParams<{ id: string }>();
  const navigate = useBotNavigate();
  const queryClient = useQueryClient();
  useBackButton(`/review/${id}`);

  const review = useQuery({ queryKey: ['review', id], queryFn: () => fetchReview(id!) });
  const revisions = useQuery({
    queryKey: ['review-revisions', id],
    queryFn: () => fetchEditorialRevisions(id!),
  });

  const [revisionId, setRevisionId] = useState<number | null>(null);
  const [title, setTitle] = useState('');
  const [note, setNote] = useState('');
  const [tags, setTags] = useState('');
  const [link, setLink] = useState('');
  const [spoiler, setSpoiler] = useState(false);
  const [order, setOrder] = useState<number[]>([]);
  const [removed, setRemoved] = useState<number[]>([]);
  const [severity, setSeverity] = useState<'minor' | 'substantive'>('minor');
  const [preview, setPreview] = useState('');
  const [message, setMessage] = useState<string | null>(null);

  const active: EditorialRevision | undefined = useMemo(() => {
    const list = revisions.data?.revisions || [];
    return list.find((r) => r.id === revisionId) ?? list[list.length - 1];
  }, [revisions.data, revisionId]);

  // Load an existing draft (or the latest revision) into the form.
  useEffect(() => {
    if (!active) return;
    setRevisionId(active.id);
    const snap = active.status === 'draft' ? active.edited_snapshot : active.edited_snapshot;
    setTitle(snap.title);
    setNote(snap.note);
    setTags(snap.tags);
    setLink(snap.link);
    setSpoiler(snap.spoiler);
    setOrder(snap.media_order);
    setRemoved(snap.removed);
    setSeverity(active.severity);
  }, [active?.id]); // eslint-disable-line react-hooks/exhaustive-deps

  const editable = !active || active.status === 'draft';
  const changeSet: EditorialChangeSet = active?.change_set || {};

  const create = useMutation({
    mutationFn: () => createEditorialRevision(id!),
    onSuccess: (rev) => {
      setRevisionId(rev.id);
      void queryClient.invalidateQueries({ queryKey: ['review-revisions', id] });
      setMessage('已创建新版本草稿');
    },
    onError: (error) => setMessage(errorMessage(error)),
  });

  const save = useMutation({
    mutationFn: () =>
      updateEditorialRevision(id!, active!.id, {
        expected_version: active!.version,
        title, note, tags, link, spoiler,
        media_order: order, removed, severity,
      }),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ['review-revisions', id] });
      setMessage('草稿已保存');
    },
    onError: (error) => setMessage(errorMessage(error)),
  });

  const finalize = useMutation({
    mutationFn: () => finalizeEditorialRevision(id!, active!.id, active!.version),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ['review-revisions', id] });
      setMessage('已定稿，可以发布此版本');
    },
    onError: (error) => setMessage(errorMessage(error)),
  });

  const publish = useMutation({
    mutationFn: () => publishReview(id!, active!.id),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ['review', id] });
      void queryClient.invalidateQueries({ queryKey: ['review-queue'] });
      window.Telegram?.WebApp?.HapticFeedback?.notificationOccurred('success');
      navigate(`/review/${id}`);
    },
    onError: (error) => setMessage(errorMessage(error)),
  });

  const runPreview = async () => {
    if (!active) return;
    try {
      if (active.status === 'draft') await save.mutateAsync();
      const result = await previewEditorialRevision(id!, active.id);
      setPreview(
        new DOMParser().parseFromString(result.caption, 'text/html').body.textContent || '',
      );
    } catch (error) {
      setMessage(errorMessage(error));
    }
  };

  if (review.isLoading || revisions.isLoading) {
    return (
      <div className="page-loading">
        <Spinner size="m" />
      </div>
    );
  }
  if (review.isError || !review.data) {
    return (
      <div className="stack">
        <PageHeader title="编辑后发布" />
        <EmptyState title="未找到该审核" />
      </div>
    );
  }
  const item = review.data;
  const media = item.media || [];

  const move = (index: number, delta: number) => {
    const next = [...order];
    const at = next.indexOf(index);
    const to = at + delta;
    if (at < 0 || to < 0 || to >= next.length) return;
    [next[at], next[to]] = [next[to], next[at]];
    setOrder(next);
  };
  const toggleRemoved = (index: number) => {
    setRemoved((current) =>
      current.includes(index) ? current.filter((i) => i !== index) : [...current, index],
    );
  };

  if (!active) {
    return (
      <div className="stack">
        <PageHeader title="编辑后发布" subtitle={`审核 #${item.id}`} />
        <EmptyState
          title="还没有编辑版本"
          hint="创建一个版本后即可修改标题、标签、简介和附件顺序，原稿不会被删除。"
          action={
            <Button
              stretched
              loading={create.isPending}
              data-testid="create-revision"
              onClick={() => void create.mutateAsync()}
            >
              开始编辑（创建版本 1）
            </Button>
          }
        />
      </div>
    );
  }

  return (
    <div className="stack">
      <PageHeader
        title="编辑后发布"
        subtitle={`审核 #${item.id} · ${item.title || '（无标题）'}`}
      />

      <div className="card">
        <div className="card__row card__row--static">
          <div className="card__row-meta">当前版本</div>
          <div className="card__row-foot">
            <StatusBadge tone={active.status === 'finalized' ? 'good' : 'progress'}>
              版本 {active.revision_number} · {active.status}
            </StatusBadge>
          </div>
        </div>
      </div>

      {/* 内容 */}
      <PageSection title="内容">
        <div className="stack">
          <div className="field">
            <Input placeholder="标题" value={title} disabled={!editable}
              onChange={(e) => setTitle(e.target.value)} />
          </div>
          <div className="field">
            <Input
              placeholder="标签（空格 / 英文逗号 / 中文逗号分隔）"
              value={tags} disabled={!editable}
              onChange={(e) => setTags(e.target.value)}
            />
          </div>
          <div className="field">
            <Textarea placeholder="简介" value={note} disabled={!editable}
              onChange={(e) => setNote(e.target.value)} />
          </div>
          <div className="field">
            <Input placeholder="来源链接" value={link} disabled={!editable}
              onChange={(e) => setLink(e.target.value)} />
          </div>
          <div className="card">
            <div className="card__row card__row--static">
              <div className="card__row-title">剧透</div>
              <div className="card__row-meta">{spoiler ? '发布为剧透' : '正常发布'}</div>
              <div className="card__row-foot">
                <Switch checked={spoiler} disabled={!editable}
                  onChange={(e) => setSpoiler(e.target.checked)} />
              </div>
            </div>
            <div className="card__row card__row--static">
              <div className="card__row-title">标记为实质修改</div>
              <div className="card__row-meta">
                {severity === 'substantive' ? '实质修改：将提示确认' : '轻微修改'}
              </div>
              <div className="card__row-foot">
                <Switch checked={severity === 'substantive'} disabled={!editable}
                  onChange={(e) => setSeverity(e.target.checked ? 'substantive' : 'minor')} />
              </div>
            </div>
          </div>
        </div>
      </PageSection>

      {/* 媒体 */}
      <PageSection title="媒体（排序 / 移除，原稿不会被删除）">
        <div className="stack">
          {order.map((index, position) => {
            const attachment = media.find((m) => m.index === index) || media[position];
            if (!attachment) return null;
            const isRemoved = removed.includes(index);
            return (
              <div key={index} data-testid={`edit-media-${index}`}
                style={{ opacity: isRemoved ? 0.45 : 1 }}>
                <div className="card">
                  <div className="card__row card__row--static">
                    <div className="card__row-title">#{position + 1}</div>
                    <div className="card__row-meta">
                      {isRemoved ? '本次发布已移除（原稿保留）' : attachment.filename || `附件 ${index + 1}`}
                    </div>
                    <div className="card__row-foot">
                      <Button size="s" mode="outline" disabled={!editable || position === 0}
                        data-testid={`move-up-${index}`} onClick={() => move(index, -1)}>上移</Button>
                      <Button size="s" mode="outline"
                        disabled={!editable || position === order.length - 1}
                        data-testid={`move-down-${index}`} onClick={() => move(index, 1)}>下移</Button>
                      <Button size="s" mode="outline" disabled={!editable}
                        data-testid={`toggle-remove-${index}`} onClick={() => toggleRemoved(index)}>
                        {isRemoved ? '恢复' : '移除'}
                      </Button>
                    </div>
                  </div>
                </div>
                <SubmissionMedia path={`/reviews/${item.id}/media/${index}`}
                  attachment={attachment} />
              </div>
            );
          })}
        </div>
      </PageSection>

      {/* 修改摘要 */}
      <PageSection title="修改摘要（服务端生成）">
        <div className="card">
          <div className="card__row card__row--static" data-testid="change-summary">
            <div className="card__row-title">
              {Object.keys(changeSet).length ? '已修改字段' : '尚无修改'}
            </div>
            {active.summary && <div className="card__row-meta">{active.summary}</div>}
            <div className="tag-list">
              {changeSet.title && <Chip>标题</Chip>}
              {changeSet.note && <Chip>简介</Chip>}
              {changeSet.tags && <Chip>标签</Chip>}
              {changeSet.link && <Chip>链接</Chip>}
              {changeSet.spoiler && <Chip>剧透</Chip>}
              {changeSet.media?.reordered && <Chip>顺序</Chip>}
              {!!changeSet.media?.removed?.length && <Chip>移除附件</Chip>}
            </div>
          </div>
        </div>
      </PageSection>

      {/* 发布前警告 */}
      {severity === 'substantive' && (
        <div className="error-box" data-testid="substantive-warning">
          此修改可能改变投稿原意，请确认后发布。
        </div>
      )}
      {message && <div className="error-box" data-testid="edit-message">{message}</div>}

      <ActionBar
        primary={
          <Button
            size="l"
            stretched
            loading={publish.isPending}
            disabled={active.status !== 'finalized'}
            data-testid="publish-revision"
            onClick={() => void publish.mutateAsync()}
          >
            发布此版本
          </Button>
        }
        secondary={
          <>
            <Button size="m" mode="outline" loading={save.isPending} disabled={!editable}
              data-testid="save-draft" onClick={() => void save.mutateAsync()}>保存草稿</Button>
            <Button size="m" mode="bezeled" data-testid="preview" onClick={() => void runPreview()}>
              预览
            </Button>
            <Button size="m" mode="bezeled" loading={finalize.isPending} disabled={!editable}
              data-testid="finalize" onClick={() => void finalize.mutateAsync()}>
              定稿
            </Button>
          </>
        }
        note={active.status === 'finalized' ? undefined : '定稿后才能发布此版本。'}
      />

      {preview && (
        <PageSection title="发布效果">
          <div data-testid="edit-preview" className="post-detail__note">
            {preview}
          </div>
        </PageSection>
      )}
    </div>
  );
}

function errorMessage(error: unknown): string {
  if (error instanceof ApiError && error.status === 409) {
    return '此投稿已被其他审核员更新，请刷新后继续编辑。';
  }
  return error instanceof ApiError ? error.message : (error as Error).message;
}
