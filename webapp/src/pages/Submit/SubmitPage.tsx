import { useEffect, useMemo, useState } from 'react';
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
import { useNavigate } from 'react-router-dom';
import { apiFetch, hasSession } from '../../api/client';

/**
 * Submission form (Mini App, §18-§23, §uppy).
 *
 * Framework ownership: Uppy owns attachment state (selection, restrictions,
 * duplicate detection, remove, progress, errors) through its OFFICIAL React
 * integration (`@uppy/react` Dashboard + hooks) — no imperative plugin mounting
 * and no hand-written file manager. TelePost only builds the business request:
 * one multipart POST with every file + metadata + a STABLE idempotency key, so
 * a double tap or a transport retry can never create two review items.
 */
const MAX_FILES = 20;
const MAX_FILE_BYTES = 50 * 1024 * 1024;

function formatSize(bytes?: number | null): string {
  if (!bytes) return '';
  const mb = bytes / (1024 * 1024);
  return mb >= 1 ? `${mb.toFixed(1)} MB` : `${Math.max(1, Math.round(bytes / 1024))} KB`;
}

export function SubmitPage() {
  const navigate = useNavigate();
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
  const selected = Object.values(files);

  const doSubmit = async () => {
    if (selected.length === 0) {
      props.setSnack('请先添加至少一个文件');
      return;
    }
    if (!props.tags.trim()) {
      props.setSnack('标签为必填项');
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
    form.append('link', props.link.startsWith('http') ? props.link : '');
    form.append('anonymous', String(props.anonymous));
    form.append('spoiler', String(props.spoiler));
    form.append('idempotency_key', idempotencyKey);
    props.setSubmitting(true);
    props.setSnack('提交中…');
    try {
      await apiFetch('/submissions', { method: 'POST', body: form });
      props.setSnack('投稿已提交 ✅');
      window.Telegram?.WebApp?.HapticFeedback?.notificationOccurred('success');
      setTimeout(props.onSubmitted, 900);
    } catch (error) {
      props.setSnack(`提交失败：${(error as Error).message}`);
      window.Telegram?.WebApp?.HapticFeedback?.notificationOccurred('error');
    } finally {
      props.setSubmitting(false);
    }
  };

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
          height={260}
          hideUploadButton
          showProgressDetails
          proudlyDisplayPoweredByUppy={false}
        />
        <div style={{ padding: '0 16px' }}>
          <Input
            placeholder="标签（必填，如 #示例 #壁纸）"
            value={props.tags}
            onChange={(e) => props.setTags(e.target.value)}
          />
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
        <Button
          size="l"
          stretched
          loading={props.submitting}
          disabled={props.submitting}
          data-testid="submit"
          onClick={() => void doSubmit()}
        >
          {props.submitting ? '提交中…' : '提交审核'}
        </Button>
        <div className="mutation-help" style={{ marginTop: 8 }}>
          重复点击不会产生重复投稿（同一幂等键，§23）。
        </div>
      </div>
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