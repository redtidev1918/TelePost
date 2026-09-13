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
import Dashboard from '@uppy/dashboard';
import '@uppy/core/dist/style.min.css';
import '@uppy/dashboard/dist/style.min.css';
import { useNavigate } from 'react-router-dom';
import { apiFetch, hasSession } from '../../api/client';

/**
 * Submission form (Mini App, §18-§23).
 *
 * Uppy owns selection/preview/progress/retry/cancel UI; the SUBMIT action
 * builds one multipart POST to the existing TelePost /api/v1/submissions with
 * all files + metadata + a STABLE idempotency_key, so a double tap or network
 * retry never creates two review items (§23, §169).
 */
export function SubmitPage() {
  const navigate = useNavigate();
  const containerRef = useRef<HTMLDivElement>(null);
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

  const uppy = useMemo(
    () =>
      new Uppy({
        autoProceed: false,
        restrictions: { maxNumberOfFiles: 20, maxFileSize: 50 * 1024 * 1024 },
      }),
    [],
  );

  useEffect(() => {
    void (async () => {
      new Dashboard(uppy, {
        target: containerRef.current!,
        inline: true,
        height: 300,
        showProgressDetails: true,
        proudlyDisplayPoweredByUppy: false,
        hideUploadButton: true,
      });
      uppy.on('file-added', (file: { name?: string }) => setSnack(`已添加 ${file.name ?? ''}`));
      uppy.on('file-removed', () => setSnack('已移除文件'));
      return () => uppy.destroy();
    })();
  }, [uppy]);

  const doSubmit = async () => {
    if (uppy.getFiles().length === 0) {
      setSnack('请先选择至少一个文件');
      return;
    }
    if (!tags.trim()) {
      setSnack('标签为必填项');
      return;
    }
    if (!hasSession()) {
      setSnack('会话已过期，请关闭并重新打开小程序');
      return;
    }
    const form = new FormData();
    for (const file of uppy.getFiles()) {
      const blob =
        file.data instanceof Blob
          ? (file.data as Blob)
          : new Blob([file.data as ArrayBuffer]);
      form.append('files', blob, file.name);
    }
    form.append('tags', tags);
    form.append('title', title);
    form.append('note', note);
    form.append('link', link.startsWith('http') ? link : '');
    form.append('anonymous', String(anonymous));
    form.append('spoiler', String(spoiler));
    form.append('idempotency_key', idempotencyKey);
    setSubmitting(true);
    setSnack('提交中…');
    try {
      await apiFetch('/submissions', { method: 'POST', body: form });
      setSnack('投稿已提交 ✅');
      window.Telegram?.WebApp?.HapticFeedback?.notificationOccurred('success');
      setTimeout(() => navigate('/mine'), 900);
    } catch (error) {
      setSnack(`提交失败：${(error as Error).message}`);
      window.Telegram?.WebApp?.HapticFeedback?.notificationOccurred('error');
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <div>
      <Section header="投稿">
        <div ref={containerRef} />
        <Cell subtitle="最多 20 个文件，单文件 ≤ 50MB（在上方面板选择/拖拽）">
          媒体 / 文件
        </Cell>
        <div style={{ padding: '0 16px' }}>
          <Input
            placeholder="标签（必填，如 #示例 #壁纸）"
            value={tags}
            onChange={(e) => setTags(e.target.value)}
          />
        </div>
        <div style={{ padding: '0 16px 12px' }}>
          <Input
            placeholder="标题（可选）"
            value={title}
            onChange={(e) => setTitle(e.target.value)}
          />
        </div>
        <div style={{ padding: '0 16px 12px' }}>
          <Textarea
            placeholder="备注（可选）"
            value={note}
            onChange={(e) => setNote(e.target.value)}
          />
        </div>
        <div style={{ padding: '0 16px 12px' }}>
          <Input
            placeholder="来源链接（可选，http/https）"
            value={link}
            onChange={(e) => setLink(e.target.value)}
          />
        </div>
        <Cell subtitle={anonymous ? '不展示署名' : '展示署名'}>
          <Switch checked={anonymous} onChange={(e) => setAnonymous(e.target.checked)} />
          匿名投稿
        </Cell>
        <Cell subtitle={spoiler ? '通过后以剧透发布' : '正常发布'}>
          <Switch checked={spoiler} onChange={(e) => setSpoiler(e.target.checked)} />
          剧透
        </Cell>
        <Cell
          subtitle={
            <div style={{ whiteSpace: 'pre-wrap', wordBreak: 'break-word' }}>
              {[tags && `🏷 ${tags}`, title, note, link && link]
                .filter(Boolean)
                .join('\n')}
            </div>
          }
        >
          预览
        </Cell>
      </Section>
      <div style={{ padding: '0 16px 12px', marginTop: 8 }}>
        <Button size="l" stretched loading={submitting} disabled={submitting} onClick={() => void doSubmit()}>
          {submitting ? '提交中…' : '提交审核'}
        </Button>
        <div className="mutation-help" style={{ marginTop: 8 }}>
          重复点击不会产生重复投稿（同一幂等键，§23）。
        </div>
      </div>
      {snack && <Snackbar onClose={() => setSnack(null)} duration={3000} children={snack} />}
    </div>
  );
}
