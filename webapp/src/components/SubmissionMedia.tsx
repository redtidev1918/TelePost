import { tr } from "../lib/i18n";
import { useTranslation } from 'react-i18next';
import { useEffect, useState } from 'react';
import { Button, Cell } from '@telegram-apps/telegram-ui';
import { useQuery } from '@tanstack/react-query';
import { apiBlob } from '../api/client';

export interface SubmissionAttachment {
  index: number;
  kind: string;
  filename?: string | null;
}

/** Both owner and reviewer endpoints stay authenticated; object URLs live only here. */
export function SubmissionMedia({ path, attachment }: {
  path: string;
  attachment: SubmissionAttachment;
}) {
  useTranslation();
  const image = attachment.kind === 'photo';
  const [requested, setRequested] = useState(false);
  const [url, setUrl] = useState('');
  const media = useQuery({
    queryKey: ['media', path, image ? 'preview' : 'original'],
    queryFn: ({ signal }) => apiBlob(`${path}?variant=${image ? 'preview' : 'original'}`, signal),
    enabled: image || requested,
    gcTime: 0,
    retry: false,
  });
  useEffect(() => {
    if (!media.data) return;
    const objectUrl = URL.createObjectURL(media.data);
    setUrl(objectUrl);
    return () => URL.revokeObjectURL(objectUrl);
  }, [media.data]);
  const label = attachment.filename || tr("附件 {{p0}}", {p0: attachment.index + 1});
  return (
    <div style={{ padding: '0 16px 12px' }}>
      <Cell>{label}</Cell>
      {media.isError ? (
        <div role="alert">
          {(media.error as Error).message}
          <Button size="s" onClick={() => void media.refetch()}>{tr("重试加载")}</Button>
        </div>
      ) : url ? (
        image ? <img src={url} alt={label} style={{ width: '100%', borderRadius: 12 }} />
          : attachment.kind === 'video' || attachment.kind === 'animation'
            ? <video src={url} controls style={{ width: '100%' }} />
            : attachment.kind === 'audio'
              ? <audio src={url} controls style={{ width: '100%' }} />
              : <a href={url} download={label}>{tr("下载")}{label}</a>
      ) : (
        <Button size="s" loading={media.isFetching} disabled={media.isFetching}
          onClick={() => setRequested(true)}>
          {media.isFetching ? tr("正在加载附件…") : tr("查看附件")}
        </Button>
      )}
    </div>
  );
}
