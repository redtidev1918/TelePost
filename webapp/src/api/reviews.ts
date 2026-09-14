import { apiBase, apiFetch } from './client';

export interface ReviewSummary {
  review_id: number;
  title: string;
  tags: string[];
  media_count: number;
  document_count: number;
  spoiler: boolean;
  source_label: string | null;
  created_at: string;
  status: string;
}

export interface ReviewMedia {
  index: number;
  kind: string;
  file_id: string | null;
  filename: string | null;
  mime_type?: string | null;
  thumbnail_file_id?: string | null;
}

export interface ReviewDetail {
  id: number;
  status: string;
  title: string;
  note: string;
  tags: string[];
  link: string;
  anonymous: boolean;
  spoiler: boolean;
  submitter_name: string | null;
  submitter_id: number | null;
  source_label: string | null;
  source_ref: string | null;
  scheduled_at: string | null;
  source: string;
  target_id: string;
  media: ReviewMedia[];
  created_at: string;
  updated_at: string;
  error: string;
}

export interface ReviewActionResult {
  review_id: number;
  status: string;
  reused: boolean;
  message_id: number | null;
  link: string | null;
}

export interface RefetchAttempt {
  request_id: string;
  state: 'requested' | 'admitted' | 'running' | 'replaced' | 'no_alternative'
    | 'failed' | 'obsolete';
  generation: number;
  source_review_id: number;
  result_candidate_id: string;
  slot_id: string;
  failure_code: string;
  scanned: number;
  skipped_duplicate: number;
  skipped_invalid: number;
  skipped_unavailable: number;
  created_at: number;
  finished_at: number | null;
}

export interface ReviewPage {
  items: ReviewSummary[];
  next_cursor: string | null;
}

export function fetchReviewQueue(
  cursor?: string | null,
  query?: Record<string, string>,
): Promise<ReviewPage> {
  const params = new URLSearchParams();
  if (cursor) params.set('cursor', cursor);
  for (const [k, v] of Object.entries(query || {})) {
    if (v) params.set(k, v);
  }
  const qs = params.toString();
  return apiFetch<ReviewPage>(`/reviews${qs ? `?${qs}` : ''}`);
}

export function fetchReview(id: number | string): Promise<ReviewDetail> {
  return apiFetch<ReviewDetail>(`/reviews/${id}`);
}

/** Media preview URL (bounded by server; never a local filesystem path §29). */
export function reviewMediaUrl(
  id: number | string,
  index: number,
  variant: 'thumbnail' | 'preview' | 'original' = 'preview',
): string {
  return `${apiBase()}/reviews/${id}/media/${index}?variant=${variant}`;
}

export function approveReview(
  id: number | string,
  spoiler?: boolean,
): Promise<ReviewActionResult> {
  return apiFetch(`/reviews/${id}/approve`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ spoiler: spoiler ?? undefined }),
  });
}

export function rejectReview(
  id: number | string,
  reason?: string,
): Promise<ReviewActionResult> {
  return apiFetch(`/reviews/${id}/reject`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ reason: reason || undefined }),
  });
}

export function setSpoiler(
  id: number | string,
  spoiler: boolean,
): Promise<ReviewActionResult> {
  return apiFetch(`/reviews/${id}/spoiler`, {
    method: 'PATCH',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ spoiler }),
  });
}

/** Refetch lives behind the review detail (only meaningful for pending). */
export function requestRefetch(id: number | string): Promise<{ request_id: string }> {
  return apiFetch(`/reviews/${id}/refetch`, { method: 'POST' });
}

export function fetchRefetchAttempt(
  id: number | string,
): Promise<{ attempt: RefetchAttempt | null; lineage: { generation: number; candidate_id: string; status: string }[] }> {
  return apiFetch(`/reviews/${id}/refetch`);
}
