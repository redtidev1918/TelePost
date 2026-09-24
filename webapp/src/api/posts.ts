import { apiBlob, apiFetch } from './client';

/** Public hot post DTO. Internal identity and file_ids never cross the wire. */
export interface PostSummary {
  message_id: number;
  title: string;
  tags: string[];
  link: string;
  publish_time: number;
  heat_score: number;
  reactions: number;
  content_type: string;
  media_count: number;
}

export interface PostDetail extends PostSummary {
  note: string;
}

export interface HotPostPage {
  items: PostSummary[];
  next_cursor: string | null;
}

export type HotScope = 'all' | 'week';

export function fetchHotPosts(
  scope: HotScope,
  cursor?: string | null,
): Promise<HotPostPage> {
  const params = new URLSearchParams({ scope, limit: '10' });
  if (cursor) params.set('cursor', cursor);
  return apiFetch<HotPostPage>(`/posts/hot?${params.toString()}`);
}

export function fetchPost(messageId: number | string): Promise<PostDetail> {
  return apiFetch<PostDetail>(`/posts/${messageId}`);
}

/** Authenticated media bytes are proxied server-side and never expose file ids. */
export function fetchPostMedia(
  messageId: number | string,
  index = 0,
  variant: 'thumbnail' | 'preview' = 'preview',
  signal?: AbortSignal,
): Promise<Blob> {
  return apiBlob(`/posts/${messageId}/media/${index}?variant=${variant}`, signal);
}

export function formatHeat(value: number): string {
  return Number.isFinite(value) ? value.toFixed(1) : '0.0';
}

export function formatPublishedAt(seconds: number): string {
  if (!seconds) return '';
  const date = new Date(seconds * 1000);
  return `${date.getMonth() + 1}月${date.getDate()}日 ${String(date.getHours()).padStart(2, '0')}:${String(date.getMinutes()).padStart(2, '0')}`;
}
