import type { SubmissionAttachment } from '../components/SubmissionMedia';
import { apiFetch } from './client';

export interface MePayload {
  telegram_user_id: number;
  name: string;
  /** Presentation metadata (§identity) — never an ownership key. */
  username?: string;
  display_name?: string;
  surface: string;
  submissions_last_hour: number;
  rate_limit_per_hour: number;
  /** Added by the Mini App me fetch when a session principal is used. */
  roles?: string[];
}

/**
 * One LOGICAL submission: a review chain, not a raw review row.
 *
 * A refetch replacement is a new generation of the SAME submission, so the
 * server returns the chain head with chain aggregates and the list never shows
 * A/B/C as three items. Internal lineage/audit fields are not exposed.
 */
export interface LogicalSubmission {
  submission_id: string;
  review_chain_id: string;
  current_review_id: number;
  /** User-facing status: preparing|in_review|publishing|published|rejected|failed|expired */
  status: string;
  title: string;
  tags: string[];
  media_count: number;
  document_count: number;
  spoiler: boolean;
  created_at: number;
  updated_at: number;
  generation: number;
  refetch_count: number;
}

export interface LogicalSubmissionDetail extends LogicalSubmission {
  note: string;
  link: string;
  media: SubmissionAttachment[];
}

export interface OwnSubmissionPage {
  items: LogicalSubmission[];
  next_cursor: string | null;
}

/** User-facing status groups used by the Mine filters. */
export type MineFilter = 'all' | 'active' | 'done' | 'other';

const ACTIVE = new Set(['preparing', 'in_review', 'publishing']);
const DONE = new Set(['published']);

export function matchesFilter(status: string, filter: MineFilter): boolean {
  if (filter === 'all') return true;
  if (filter === 'active') return ACTIVE.has(status);
  if (filter === 'done') return DONE.has(status);
  return !ACTIVE.has(status) && !DONE.has(status);
}

export function fetchMe(): Promise<MePayload> {
  return apiFetch<MePayload>('/me');
}

export function fetchMySubmissions(
  cursor?: string | null,
): Promise<OwnSubmissionPage> {
  const query = cursor ? `?cursor=${encodeURIComponent(cursor)}` : '';
  return apiFetch<OwnSubmissionPage>(`/me/submissions${query}`);
}

export function fetchMySubmission(
  reviewId: string | number,
): Promise<LogicalSubmissionDetail> {
  return apiFetch<LogicalSubmissionDetail>(`/me/submissions/${reviewId}`);
}