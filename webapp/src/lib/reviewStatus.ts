import { tr } from "./i18n";
/** User-facing review outcome labels; DB states never leak verbatim. */
export const REVIEW_STATUS_LABELS = (): Record<string, string> => ({
  published: tr("已发布"),
  rejected: tr("已拒绝"),
  failed: tr("失败"),
  expired: tr("已过期"),
  superseded: tr("已被替换"),
});

export function reviewStatusLabel(status: string): string {
  return REVIEW_STATUS_LABELS()[status] || status;
}
