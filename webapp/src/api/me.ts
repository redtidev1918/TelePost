import { apiFetch } from './client';

export interface MePayload {
  telegram_user_id: number;
  name: string;
  surface: string;
  submissions_last_hour: number;
  rate_limit_per_hour: number;
  /** Added by the Mini App me fetch when a session principal is used. */
  roles?: string[];
}

export interface OwnSubmission {
  review_id: number;
  status: string;
  title: string;
  tags: string[];
  media_count: number;
  document_count: number;
  spoiler: boolean;
  created_at: number;
  source: string;
}

export interface OwnSubmissionPage {
  items: OwnSubmission[];
  next_cursor: string | null;
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
