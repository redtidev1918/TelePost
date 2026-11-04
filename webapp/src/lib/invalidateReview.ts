import type { QueryClient } from '@tanstack/react-query';

/** All projections of a review can change, including after a conflict/timeout. */
export function invalidateReview(client: QueryClient) {
  return Promise.all([
    'review', 'review-refetch', 'review-queue', 'review-history', 'review-history-peek',
    'my-submissions', 'my-submission', 'editorial-history', 'review-revisions',
    'hot-posts', 'hot-preview', 'me',
  ].map((key) => client.invalidateQueries({ queryKey: [key] })));
}
