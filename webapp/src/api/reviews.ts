import { apiFetch } from './client';

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

// ---- Editorial Revision (§editorial) --------------------------------------

export interface EditorialSnapshot {
  title: string;
  note: string;
  tags: string;
  link: string;
  spoiler: boolean;
  media_order: number[];
  removed: number[];
}

export interface EditorialChangeSet {
  title?: { before: string; after: string };
  note?: { changed: boolean };
  tags?: { added: string[]; removed: string[] };
  link?: { before: string; after: string };
  spoiler?: { before: boolean; after: boolean };
  media?: { reordered: boolean; removed: number[] };
}

export interface EditorialRevision {
  id: number;
  review_id: number;
  revision_number: number;
  status: 'draft' | 'finalized' | 'published' | 'superseded';
  severity: 'minor' | 'substantive';
  summary: string;
  change_set: EditorialChangeSet;
  base_snapshot: EditorialSnapshot;
  edited_snapshot: EditorialSnapshot;
  version: number;
  editor_display: string;
  created_at: number;
  updated_at: number;
  finalized_at: number | null;
  published_at: number | null;
  published_message_id: number | null;
  published_snapshot: EditorialSnapshot;
}

export interface SubmitterEditorialRevision {
  revision_number: number;
  status: string;
  summary: string;
  change_set: EditorialChangeSet;
  edited_snapshot: EditorialSnapshot;
  published_snapshot: EditorialSnapshot;
  finalized_at: number | null;
  published_at: number | null;
  published_message_id: number | null;
  editor_display: string;
}

export interface SubmitterEditorialHistory {
  review_id: number;
  status: string;
  edited_before_publication: boolean;
  published_message_id: number | null;
  revisions: SubmitterEditorialRevision[];
}

export function fetchEditorialRevisions(
  id: number | string,
): Promise<{ revisions: EditorialRevision[] }> {
  return apiFetch(`/reviews/${id}/editorial-revisions`);
}

export function createEditorialRevision(
  id: number | string,
): Promise<EditorialRevision> {
  return apiFetch(`/reviews/${id}/editorial-revisions`, { method: 'POST' });
}

export interface EditorialPatch {
  expected_version: number;
  title?: string;
  note?: string;
  tags?: string;
  link?: string;
  spoiler?: boolean;
  media_order?: number[];
  removed?: number[];
  severity?: 'minor' | 'substantive';
}

export function updateEditorialRevision(
  id: number | string,
  revisionId: number,
  patch: EditorialPatch,
): Promise<EditorialRevision> {
  return apiFetch(`/reviews/${id}/editorial-revisions/${revisionId}`, {
    method: 'PATCH',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(patch),
  });
}

export function finalizeEditorialRevision(
  id: number | string,
  revisionId: number,
  expectedVersion: number,
): Promise<EditorialRevision> {
  return apiFetch(`/reviews/${id}/editorial-revisions/${revisionId}/finalize`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ expected_version: expectedVersion }),
  });
}

export function previewEditorialRevision(
  id: number | string,
  revisionId: number,
  patch?: Partial<EditorialPatch>,
): Promise<{ caption: string }> {
  return apiFetch(`/reviews/${id}/editorial-revisions/${revisionId}/preview`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(patch || {}),
  });
}

/** Publish the ORIGINAL (revisionId null) or a FINALIZED revision. */
export function publishReview(
  id: number | string,
  revisionId?: number | null,
): Promise<ReviewActionResult> {
  return apiFetch(`/reviews/${id}/publish`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(revisionId ? { revision_id: revisionId } : {}),
  });
}

export function fetchEditorialHistory(
  id: number | string,
): Promise<SubmitterEditorialHistory> {
  return apiFetch(`/me/submissions/${id}/editorial-history`);
}
