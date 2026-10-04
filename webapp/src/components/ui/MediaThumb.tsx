import { useEffect, useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { fetchPostMedia } from '../../api/posts';
import { useInView } from '../../lib/useInView';

/**
 * Authenticated, lazily fetched post media (§ui-kit).
 *
 * - only requests bytes when the element approaches the viewport (bounded cost)
 * - always renders a neutral placeholder while loading / on failure, so a
 *   missing preview never changes the text layout or breaks the card
 * - never leaks Telegram file ids: the server proxies the bytes
 * - renders inline elements only, so a whole card can be a real <button>
 */
export function MediaThumb({
  messageId,
  index = 0,
  alt = '',
  testId = 'media-thumb',
  enabled = true,
  className = 'media-card__media',
  imgClassName = 'media-card__img',
  emptyLabel,
}: {
  messageId: number | string;
  index?: number;
  alt?: string;
  testId?: string;
  enabled?: boolean;
  className?: string;
  imgClassName?: string;
  emptyLabel?: string;
}) {
  const { ref, inView } = useInView<HTMLSpanElement>();
  const [url, setUrl] = useState('');
  const query = useQuery({
    queryKey: ['post-media-thumb', String(messageId), index],
    queryFn: ({ signal }) => fetchPostMedia(messageId, index, 'preview', signal),
    enabled: enabled && inView,
    retry: false,
    staleTime: 5 * 60_000,
  });
  useEffect(() => {
    if (!query.data) return;
    const objectUrl = URL.createObjectURL(query.data);
    setUrl(objectUrl);
    return () => URL.revokeObjectURL(objectUrl);
  }, [query.data]);

  return (
    <span ref={ref} className={className} data-testid={testId}>
      {url ? (
        <img className={imgClassName} src={url} alt={alt} loading="lazy" decoding="async" />
      ) : (
        <span className="media-card__media--empty">{emptyLabel ?? ''}</span>
      )}
    </span>
  );
}
