import { useBotNavigate } from '../../lib/useBotNavigate';
import { useEffect, useMemo, useRef, useState } from 'react';
import {
  Button,
  Cell,
  Section,
  Input,
  Textarea,
  Switch,
  Snackbar,
} from '@telegram-apps/telegram-ui';
import Uppy from '@uppy/core';
import { Dashboard, UppyContextProvider, useFileInput, useUppyState } from '@uppy/react';
import '@uppy/core/dist/style.min.css';
import '@uppy/dashboard/dist/style.min.css';
import '@uppy/react/dist/styles.css';
import { useQueryClient } from '@tanstack/react-query';
import { apiFetch, getSessionUser, hasSession } from '../../api/client';
import { formatSubmitter } from '../../lib/formatSubmitter';

/**
 * Submission form (Mini App, §18-§23, §uppy).
 *
 * Framework ownership: Uppy owns attachment state (selection, restrictions,
 * duplicate detection, remove, progress, errors) through its OFFICIAL React
 * integration — no imperative plugin mounting and no hand-written file manager.
 * TelePost only builds the business request: one multipart POST with every file
 * + metadata + a STABLE idempotency key.
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
  const [caption, setCaption] = useState('');
  const [previewOpen, setPreviewOpen] = useState(false);

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
      // The server owns caption formatting. Render its text safely, never HTML.
      setCaption(new DOMParser().parseFromString(result.caption, 'text/html').body.textContent || '');
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

  const sessionUser = getSessionUser();
  const submitterLine = formatSubmitter(
    sessionUser?.username,
    sessionUser?.display_name,
    sessionUser?.telegram_user_id,
  );

  return (
    <div>
      <Section header="投稿">
        <div className="attach-actions">
          {/* Real, working picker entry: Uppy's official file-input hook. */}
          <Button
            size="m"
            mode="outline"
            stretched
            data-testid="add-files"
            {...fileInput.getButtonProps()}
          >
            ＋ 添加媒体或文件
          </Button>
          {selected.length > 0 && (
            <Button size="m" mode="outline" data-testid="clear-files"
              onClick={() => uppy.removeFiles(Object.keys(files))}>
              🗑 清空附件
            </Button>
          )}
          <input
            {...fileInput.getInputProps()}
            data-testid="file-input"
            style={{ display: 'none' }}
          />
        </div>
        <Cell
          subtitle={
            selected.length
              ? selected
                  .map((file) => `${file.name ?? 'file'} · ${formatSize(file.size)}`)
                  .join('\n')
              : `最多 ${MAX_FILES} 个文件，单文件 ≤ 50MB`
          }
          data-testid="selected-files"
        >
          {selected.length ? `已选择 ${selected.length} 个文件` : '尚未选择文件'}
        </Cell>
        {/* @uppy/react renders the Dashboard inline by default (React-owned
            mount/unmount); no imperative plugin mounting here. */}
        <Dashboard
          uppy={uppy}
          height={220}
          hideUploadButton
          showProgressDetails
          proudlyDisplayPoweredByUppy={false}
        />
        <div style={{ padding: '0 16px' }}>
          <Input
            placeholder="标签（必填，可用空格或逗号分隔）"
            value={props.tags}
            onChange={(e) => props.setTags(e.target.value)}
          />
          <div className="tag-hint" data-testid="tag-hint">{TAG_HINT}</div>
        </div>
        <div style={{ padding: '0 16px 12px' }}>
          <Input
            placeholder="标题（可选）"
            value={props.title}
            onChange={(e) => props.setTitle(e.target.value)}
          />
        </div>
        <div style={{ padding: '0 16px 12px' }}>
          <Textarea
            placeholder="备注（可选）"
            value={props.note}
            onChange={(e) => props.setNote(e.target.value)}
          />
        </div>
        <div style={{ padding: '0 16px 12px' }}>
          <Input
            placeholder="来源链接（可选，http/https）"
            value={props.link}
            onChange={(e) => props.setLink(e.target.value)}
          />
        </div>
        <Cell subtitle={props.anonymous ? '不展示署名' : '展示署名'}>
          <Switch
            checked={props.anonymous}
            onChange={(e) => props.setAnonymous(e.target.checked)}
          />
          匿名投稿
        </Cell>
        <Cell subtitle={props.spoiler ? '通过后以剧透发布' : '正常发布'}>
          <Switch
            checked={props.spoiler}
            onChange={(e) => props.setSpoiler(e.target.checked)}
          />
          剧透
        </Cell>
      </Section>
      <div style={{ padding: '0 16px 12px', marginTop: 8 }}>
        <Button mode="outline" stretched disabled={props.submitting}
          onClick={openPreview} style={{ marginBottom: 12 }}>预览投稿</Button>
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
        <div className="mutation-help" style={{ marginTop: 8 }}>
          提交失败时会保留附件和文字，可直接重试。
        </div>
      </div>
      {previewOpen && (
        <div data-testid="preview-panel" className="preview-panel" style={{ padding: '0 16px 12px' }}>
          <h3 style={{ margin: '12px 0 8px' }}>投稿预览</h3>
          <div
            data-testid="preview-media"
            style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(96px, 1fr))', gap: 8, marginBottom: 12 }}
          >
            {selected.map((file, index) => {
              const kind = fileKind(file);
              const url = objectUrls.get(file.id) ?? '';
              return (
                <div key={file.id} data-testid={`preview-media-${index}`}
                  style={{ textAlign: 'center' }}>
                  {kind === 'image' && url ? (
                    <img src={url} alt={file.name ?? `附件 ${index + 1}`}
                      style={{ width: '100%', borderRadius: 8, aspectRatio: '1', objectFit: 'cover' }} />
                  ) : kind === 'video' ? (
                    <video src={url || undefined} controls style={{ width: '100%', borderRadius: 8 }} />
                  ) : kind === 'audio' ? (
                    <audio src={url || undefined} controls style={{ width: '100%' }} />
                  ) : (
                    <div style={{ fontSize: 28 }}>📄</div>
                  )}
                  <div style={{ fontSize: 12, color: 'var(--tgui--subtitle_text_color)', wordBreak: 'break-all' }}>
                    {file.name ?? `附件 ${index + 1}`} · {formatSize(file.size)}
                  </div>
                </div>
              );
            })}
          </div>
          {props.title && <div>🔖 标题：{props.title}</div>}
          {props.note && <div>📝 简介：{props.note}</div>}
          {props.link && <div>🔗 链接：{props.link}</div>}
          {props.tags.trim() && <div>🏷 {props.tags.trim()}</div>}
          {submitterLine && (
            <div data-testid="preview-submitter">投稿人：{submitterLine}</div>
          )}
          {caption && (
            <div data-testid="preview-caption" style={{ whiteSpace: 'pre-wrap', margin: '8px 0' }}>
              {caption}
            </div>
          )}
          <div style={{ display: 'flex', gap: 8, marginTop: 12 }}>
            <Button mode="outline" stretched disabled={props.submitting}
              data-testid="preview-back" onClick={() => setPreviewOpen(false)}>
              返回修改
            </Button>
            <Button stretched loading={props.submitting} disabled={props.submitting}
              data-testid="preview-submit" onClick={() => void submit()}>
              {props.submitting ? '提交中…' : '提交审核'}
            </Button>
          </div>
        </div>
      )}
      {props.snack && (
        <Snackbar
          onClose={() => props.setSnack(null)}
          duration={3000}
          children={props.snack}
        />
      )}
    </div>
  );
}