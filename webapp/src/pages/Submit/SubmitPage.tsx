import { useBotNavigate } from '../../lib/useBotNavigate';
import { useEffect, useMemo, useRef, useState } from 'react';
import {
  Button,
  Input,
  Section,
  Snackbar,
  Spinner,
  Switch,
  Textarea,
} from '@telegram-apps/telegram-ui';
import Uppy from '@uppy/core';
import { Dashboard, UppyContextProvider, useFileInput, useUppyState } from '@uppy/react';
import '@uppy/core/dist/style.min.css';
import '@uppy/dashboard/dist/style.min.css';
import '@uppy/react/dist/styles.css';
import { useQueryClient } from '@tanstack/react-query';
import { apiFetch, hasSession } from '../../api/client';
import { PageHeader } from '../../components/ui/PageHeader';
import { PageSection } from '../../components/ui/PageSection';
import { ActionBar } from '../../components/ui/ActionBar';

/**
 * Submission form (Mini App, §18-§23, §uppy, §submit-ux).
 *
 * Framework ownership: Uppy owns attachment state (selection, restrictions,
 * duplicate detection, remove, progress, errors) through its OFFICIAL React
 * integration — no imperative plugin mounting and no hand-written file manager.
 * TelePost only builds the business request: one multipart POST with every file
 * + metadata + a STABLE idempotency key.
 *
 * Presentation: the page is a four-step single column — attachments, content,
 * publish settings, submit — so the phone shows one decision at a time. Uppy's
 * Dashboard is demoted to an optional tool behind a toggle: it still owns the
 * state and keeps its official React lifecycle, but it is never the first thing
 * on screen and never dominates the layout.
 *
 * Preview (§preview-ux): local media previews are rendered from Uppy state via
 * a thin browser adapter (URL.createObjectURL, revoked on remove/unmount/
 * submit), and the caption preview comes from the server formatter — so the
 * preview is side-effect free (no upload, no Telegram messages) and the shown
 * caption can never drift from the real one.
 *
 * Tags: the input shows a separator hint; the SERVER's process_tags remains the
 * single tag authority (split, dedupe, normalize, #-prefix, illegal-chars).
 */
type PickedFile = { id: string; data: unknown; name?: string; size?: number | null; type?: string };

const MAX_FILES = 20;
const MAX_FILE_BYTES = 50 * 1024 * 1024;

const TAG_HINT =
  '例如：ボテ腹, R18 pregnancy\n空格、英文逗号、中文逗号均可，无需输入 #';

function formatSize(bytes?: number | null): string {
  if (!bytes) return '';
  const mb = bytes / (1024 * 1024);
  return mb >= 1 ? `${mb.toFixed(1)} MB` : `${Math.max(1, Math.round(bytes / 1024))} KB`;
}

/** Thin presentation adapter: preview media kind from type/extension. */
function fileKind(file: { type?: string; name?: string }): 'image' | 'video' | 'audio' | 'document' {
  const t = (file.type || '').toLowerCase();
  const n = (file.name || '').toLowerCase();
  if (t.startsWith('image/') || /\.(png|jpe?g|webp|gif|bmp|avif)$/.test(n)) return 'image';
  if (t.startsWith('video/') || /\.(mp4|mkv|mov|webm|avi)$/.test(n)) return 'video';
  if (t.startsWith('audio/') || /\.(mp3|ogg|m4a|flac|wav)$/.test(n)) return 'audio';
  return 'document';
}

/**
 * Local object URLs for the CURRENT selected files (browser capability, no
 * image-processing framework). URLs are created lazily, cached per file id +
 * data identity, revoked when a file is removed and all revoked on unmount.
 */
function useObjectUrls(files: Array<{ id: string; data: unknown }>): Map<string, string> {
  const [urls, setUrls] = useState<Map<string, string>>(() => new Map());
  const cache = useRef(new Map<string, { url: string; data: unknown }>());
  useEffect(() => {
    const next = new Map<string, string>();
    const ids = new Set(files.map((f) => f.id));
    for (const [id, entry] of cache.current) {
      if (!ids.has(id)) {
        URL.revokeObjectURL(entry.url);
        cache.current.delete(id);
      }
    }
    for (const file of files) {
      let entry = cache.current.get(file.id);
      if (!entry || entry.data !== file.data) {
        if (entry) URL.revokeObjectURL(entry.url);
        const blob = file.data instanceof Blob ? file.data : new Blob([file.data as ArrayBuffer]);
        entry = { url: URL.createObjectURL(blob), data: file.data };
        cache.current.set(file.id, entry);
      }
      next.set(file.id, entry.url);
    }
    setUrls(next);
  }, [files]);
  useEffect(() => {
    const entries = [...cache.current.values()];
    return () => entries.forEach((entry) => URL.revokeObjectURL(entry.url));
  }, []);
  return urls;
}

export function SubmitPage() {
  const navigate = useBotNavigate();
  // One Uppy instance per mounted page, destroyed on unmount (§uppy lifecycle).
  const uppy = useMemo(
    () =>
      new Uppy({
        autoProceed: false,
        restrictions: {
          maxNumberOfFiles: MAX_FILES,
          maxFileSize: MAX_FILE_BYTES,
        },
      }),
    [],
  );
  useEffect(() => () => uppy.destroy(), [uppy]);

  const idempotencyKey = useMemo(
    () => (crypto.randomUUID ? crypto.randomUUID() : Math.random().toString(36).slice(2)),
    [],
  );
  const [tags, setTags] = useState('');
  const [title, setTitle] = useState('');
  const [note, setNote] = useState('');
  const [link, setLink] = useState('');
  const [anonymous, setAnonymous] = useState(false);
  const [spoiler, setSpoiler] = useState(false);
  const [snack, setSnack] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);

  return (
    <UppyContextProvider uppy={uppy}>
      <SubmitForm
        uppy={uppy}
        idempotencyKey={idempotencyKey}
        snack={snack}
        setSnack={setSnack}
        submitting={submitting}
        setSubmitting={setSubmitting}
        tags={tags}
        setTags={setTags}
        title={title}
        setTitle={setTitle}
        note={note}
        setNote={setNote}
        link={link}
        setLink={setLink}
        anonymous={anonymous}
        setAnonymous={setAnonymous}
        spoiler={spoiler}
        setSpoiler={setSpoiler}
        onSubmitted={() => navigate('/mine')}
      />
    </UppyContextProvider>
  );
}

interface FormProps {
  uppy: Uppy;
  idempotencyKey: string;
  snack: string | null;
  setSnack: (value: string | null) => void;
  submitting: boolean;
  setSubmitting: (value: boolean) => void;
  tags: string;
  setTags: (value: string) => void;
  title: string;
  setTitle: (value: string) => void;
  note: string;
  setNote: (value: string) => void;
  link: string;
  setLink: (value: string) => void;
  anonymous: boolean;
  setAnonymous: (value: boolean) => void;
  spoiler: boolean;
  setSpoiler: (value: boolean) => void;
  onSubmitted: () => void;
}

function SubmitForm(props: FormProps) {
  const { uppy, idempotencyKey } = props;
  const files = useUppyState(uppy, (state) => state.files);
  const fileInput = useFileInput({ multiple: true });
  // Uppy's files record reference only changes on real state updates; the
  // values array must NOT be recreated every render (it is useObjectUrls' dep).
  const selected = useMemo(() => Object.values(files) as PickedFile[], [files]);
  const objectUrls = useObjectUrls(selected);
  const queryClient = useQueryClient();
  const inFlight = useRef(false);
  const [previewHtml, setPreviewHtml] = useState('');
  const [previewOpen, setPreviewOpen] = useState(false);
  const [uppyOpen, setUppyOpen] = useState(false);

  /** Server-side caption preview: the SAME formatter the review group sees. */
  const refreshCaption = async () => {
    try {
      const result = await apiFetch<{ caption: string }>('/submissions/preview', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ title: props.title, tags: props.tags, note: props.note,
          link: props.link, anonymous: props.anonymous, spoiler: props.spoiler,
          media_types: selected.map((f) => ({
            image: 'photo', video: 'video', audio: 'audio', document: 'document',
          } as Record<string, string>)[fileKind(f)]) }),
      });
      setPreviewHtml(result.caption);
    } catch (error) {
      props.setSnack(`预览失败：${(error as Error).message}`);
    }
  };

  const openPreview = () => {
    if (selected.length === 0) {
      props.setSnack('请先添加至少一个文件');
      return;
    }
    if (!props.tags.trim()) {
      props.setSnack('标签为必填项');
      return;
    }
    void refreshCaption();
    setPreviewOpen(true);
  };

  // Shared submit: preview and main view use exactly the same business path.
  const submit = async () => {
    if (inFlight.current) return;
    if (selected.length === 0) {
      props.setSnack('请先添加至少一个文件');
      return;
    }
    if (!props.tags.trim()) {
      props.setSnack('标签为必填项');
      return;
    }
    if (props.link && !/^https?:\/\//i.test(props.link)) {
      props.setSnack('链接必须以 http:// 或 https:// 开头');
      return;
    }
    if (!hasSession()) {
      props.setSnack('会话已过期，请关闭并重新打开小程序');
      return;
    }
    // TelePost business adapter: one multipart request for all files + metadata.
    // Files stay in Uppy state on failure so the user can simply retry.
    const form = new FormData();
    for (const file of selected) {
      const blob =
        file.data instanceof Blob
          ? (file.data as Blob)
          : new Blob([file.data as ArrayBuffer]);
      form.append('files', blob, file.name ?? 'file');
    }
    form.append('tags', props.tags);
    form.append('title', props.title);
    form.append('note', props.note);
    form.append('link', props.link);
    form.append('anonymous', String(props.anonymous));
    form.append('spoiler', String(props.spoiler));
    form.append('idempotency_key', idempotencyKey);
    inFlight.current = true;
    props.setSubmitting(true);
    props.setSnack('提交中…');
    try {
      const result = await apiFetch<{ status: string; review_id?: number }>('/submissions', { method: 'POST', body: form });
      if (!result?.review_id || !['pending_review', 'pending', 'published', 'publishing'].includes(result.status)) {
        throw new Error('尚未确认进入审核队列，请保留草稿并重试');
      }
      void queryClient.invalidateQueries({ queryKey: ['my-submissions'] });
      props.setSnack('投稿已提交 ✅');
      window.Telegram?.WebApp?.HapticFeedback?.notificationOccurred('success');
      setTimeout(props.onSubmitted, 900);
    } catch (error) {
      props.setSnack(`提交失败：${(error as Error).message}`);
      window.Telegram?.WebApp?.HapticFeedback?.notificationOccurred('error');
    } finally {
      inFlight.current = false;
      props.setSubmitting(false);
    }
  };

  if (previewOpen) {
    return (
      <div className="stack" data-testid="preview-panel">
        <PageHeader title="投稿预览" subtitle="这是频道里将会出现的样子，确认无误后再提交。" />
        <PageSection title="附件">
          <div className="attach-grid" data-testid="preview-media">
            {selected.map((file, index) => {
              const kind = fileKind(file);
              const url = objectUrls.get(file.id) ?? '';
              return (
                <div key={file.id} className="attach-tile" data-testid={`preview-media-${index}`}>
                  {kind === 'image' && url ? (
                    <img
                      className="attach-tile__media"
                      src={url}
                      alt={file.name ?? `附件 ${index + 1}`}
                    />
                  ) : kind === 'video' ? (
                    <video className="attach-tile__media" src={url || undefined} controls />
                  ) : kind === 'audio' ? (
                    <audio src={url || undefined} controls style={{ width: '100%' }} />
                  ) : (
                    <div className="attach-tile__placeholder">📄</div>
                  )}
                  <div className="attach-tile__name">
                    {file.name ?? `附件 ${index + 1}`} · {formatSize(file.size)}
                  </div>
                </div>
              );
            })}
          </div>
        </PageSection>
        <PageSection title="频道文案">
          {previewHtml ? (
            <div
              data-testid="preview-caption"
              className="post-detail__note"
              dangerouslySetInnerHTML={{ __html: previewHtml }}
            />
          ) : (
            <div className="page-loading">
              <Spinner size="s" />
            </div>
          )}
        </PageSection>
        <ActionBar
          primary={
            <Button
              size="l"
              stretched
              loading={props.submitting}
              disabled={props.submitting}
              data-testid="preview-submit"
              onClick={() => void submit()}
            >
              {props.submitting ? '提交中…' : '提交审核'}
            </Button>
          }
          secondary={
            <Button
              mode="outline"
              stretched
              disabled={props.submitting}
              data-testid="preview-back"
              onClick={() => setPreviewOpen(false)}
            >
              返回修改
            </Button>
          }
        />
        {props.snack && (
          <Snackbar onClose={() => props.setSnack(null)} duration={3000} children={props.snack} />
        )}
      </div>
    );
  }

  return (
    <div className="stack">
      <PageHeader title="投稿" subtitle="添加附件，填写信息，然后提交审核。" />

      {/* ① 附件 */}
      <PageSection
        title={`① 附件${selected.length ? ` · ${selected.length} 个` : ''}`}
        action={selected.length > 0 ? '清空' : undefined}
        onAction={selected.length > 0 ? () => uppy.removeFiles(Object.keys(files)) : undefined}
      >
        <div className="stack">
          <Button size="m" mode="outline" stretched data-testid="add-files" {...fileInput.getButtonProps()}>
            ＋ 添加附件
          </Button>
          <input {...fileInput.getInputProps()} data-testid="file-input" style={{ display: 'none' }} />
          <Button
            size="m"
            mode="plain"
            stretched
            data-testid="clear-files"
            onClick={() => uppy.removeFiles(Object.keys(files))}
          >
            清空附件
          </Button>
          {selected.length > 0 ? (
            <div className="attach-grid" data-testid="attachment-grid">
              {selected.map((file) => {
                const kind = fileKind(file);
                const url = objectUrls.get(file.id) ?? '';
                return (
                  <div key={file.id} className="attach-tile">
                    {kind === 'image' && url ? (
                      <img className="attach-tile__media" src={url} alt={file.name ?? '附件'} />
                    ) : (
                      <div className="attach-tile__placeholder">
                        {kind === 'video' ? '🎬' : kind === 'audio' ? '🎧' : '📄'}
                      </div>
                    )}
                    <button
                      type="button"
                      className="attach-tile__remove"
                      aria-label={`移除 ${file.name ?? '附件'}`}
                      onClick={() => uppy.removeFile(file.id)}
                    >
                      ×
                    </button>
                    <div className="attach-tile__name">
                      {file.name ?? '附件'} · {formatSize(file.size)}
                    </div>
                  </div>
                );
              })}
            </div>
          ) : null}
          <div className="mutation-help" data-testid="selected-files">
            {selected.length
              ? `已选择 ${selected.length} 个文件\n${selected
                  .map((file) => `${file.name ?? 'file'} · ${formatSize(file.size)}`)
                  .join('\n')}`
              : `尚未选择文件 · 最多 ${MAX_FILES} 个，单文件 ≤ 50MB`}
          </div>
          <button
            type="button"
            className="page-section__action"
            data-testid="uppy-toggle"
            onClick={() => setUppyOpen((value) => !value)}
          >
            {uppyOpen ? '收起附件管理器' : '附件管理器（拖拽 / 进度）'}
          </button>
          <div className={`uppy-panel${uppyOpen ? ' uppy-panel--open' : ''}`} data-testid="uppy-panel">
            {/* @uppy/react renders the Dashboard inline (React-owned mount/unmount). */}
            <Dashboard
              uppy={uppy}
              height={180}
              hideUploadButton
              showProgressDetails
              proudlyDisplayPoweredByUppy={false}
            />
          </div>
        </div>
      </PageSection>

      {/* ② 投稿信息 */}
      <PageSection title="② 投稿信息">
        <div className="stack">
          <div className="field">
            <Input
              placeholder="标签（必填，可用空格或逗号分隔）"
              value={props.tags}
              onChange={(e) => props.setTags(e.target.value)}
            />
            <div className="tag-hint" data-testid="tag-hint">{TAG_HINT}</div>
          </div>
          <div className="field">
            <Input
              placeholder="标题（可选）"
              value={props.title}
              onChange={(e) => props.setTitle(e.target.value)}
            />
          </div>
          <div className="field">
            <Textarea
              placeholder="备注（可选）"
              value={props.note}
              onChange={(e) => props.setNote(e.target.value)}
            />
          </div>
          <div className="field">
            <Input
              placeholder="来源链接（可选，http/https）"
              value={props.link}
              onChange={(e) => props.setLink(e.target.value)}
            />
          </div>
        </div>
      </PageSection>

      {/* ③ 发布设置 */}
      <PageSection title="③ 发布设置">
        <Section>
          <div className="card__row card__row--static">
            <div className="card__row-title">匿名投稿</div>
            <div className="card__row-meta">{props.anonymous ? '不展示署名' : '展示署名'}</div>
            <div className="card__row-foot">
              <Switch
                checked={props.anonymous}
                onChange={(e) => props.setAnonymous(e.target.checked)}
              />
            </div>
          </div>
          <div className="card__row card__row--static">
            <div className="card__row-title">剧透</div>
            <div className="card__row-meta">{props.spoiler ? '通过后以剧透发布' : '正常发布'}</div>
            <div className="card__row-foot">
              <Switch
                checked={props.spoiler}
                onChange={(e) => props.setSpoiler(e.target.checked)}
              />
            </div>
          </div>
        </Section>
      </PageSection>

      {/* ④ 提交 */}
      <ActionBar
        primary={
          <Button
            size="l"
            stretched
            loading={props.submitting}
            disabled={props.submitting}
            data-testid="submit"
            onClick={() => void submit()}
          >
            {props.submitting ? '提交中…' : '提交审核'}
          </Button>
        }
        secondary={
          <Button
            mode="outline"
            stretched
            disabled={props.submitting}
            onClick={openPreview}
          >
            预览投稿
          </Button>
        }
        note="提交失败时会保留附件和文字，可直接重试。"
      />

      {props.snack && (
        <Snackbar onClose={() => props.setSnack(null)} duration={3000} children={props.snack} />
      )}
    </div>
  );
}
