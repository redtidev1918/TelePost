/** User-facing review outcome labels; DB states never leak verbatim. */
export const REVIEW_STATUS_LABELS: Record<string, string> = {
  published: '已发布',
  rejected: '已拒绝',
  failed: '失败',
  expired: '已过期',
  superseded: '已被替换',
};

export function reviewStatusLabel(status: string): string {
  return REVIEW_STATUS_LABELS[status] || status;
}
