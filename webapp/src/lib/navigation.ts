import { tr } from "./i18n";
/**
 * Mini App information architecture (§ia).
 *
 * One bottom bar, MAX 5 items, for every role. Reviewer-only and admin-only
 * surfaces are NOT extra tabs: they live inside the workspace behind the 5th
 * item, so the bar stays thumb-sized on a 320px phone.
 *
 *   普通用户   首页 | 热门 | 投稿 | 我的 | 更多
 *   reviewer   首页 | 热门 | 投稿 | 我的 | 审核     (更多 via the 审核 header)
 *   admin      同上；管理入口在「更多」里，不额外占一个 Tab
 *
 * Server-side RBAC is still the only authority: this module only decides which
 * entries are OFFERED, never what a session is allowed to do.
 */
export interface NavItem {
  path: string;
  label: string;
}

export const MAX_TAB_COUNT = 5;

export function navigationForSpace(isReviewer: boolean, isAdmin = false): NavItem[] {
  const nav: NavItem[] = [
    { path: '/', label: tr("首页") },
    { path: '/hot', label: tr("热门") },
    { path: '/submit', label: tr("投稿") },
    { path: '/mine', label: tr("我的") },
  ];
  // The 5th slot is the reviewer/admin workspace when the session has one,
  // otherwise the catch-all 更多 hub.
  nav.push(isReviewer ? { path: '/review', label: tr("审核") } : { path: '/more', label: tr("更多") });
  if (nav.length > MAX_TAB_COUNT) nav.length = MAX_TAB_COUNT;
  void isAdmin; // admin reaches 管理 from 更多 / the 审核 workspace, never a 6th tab
  return nav;
}

/**
 * Tab ownership for the CURRENT route.
 *
 * Deep routes belong to their section: /mine/:id and /mine/:id/editorial stay on
 * 我的, /review/:id and /review/:id/edit stay on 审核, /post/:id stays on 热门.
 * Exactly one tab can match, so a detail page never lights up two tabs, and a
 * page outside the five sections (e.g. /admin) lights up none.
 */
export function isNavItemActive(item: string, pathname: string): boolean {
  switch (item) {
    case '/':
      return pathname === '/';
    case '/hot':
      return pathname === '/hot' || pathname === '/hotweek' || pathname.startsWith('/post/');
    case '/submit':
      return pathname === '/submit';
    case '/mine':
      return pathname === '/mine' || pathname.startsWith('/mine/');
    case '/review':
      return pathname === '/review' || pathname.startsWith('/review/');
    case '/more':
      return pathname === '/more';
    default:
      return pathname === item;
  }
}

/** The workspace that owns a route, used for headers and secondary entries. */
export function sectionForPath(pathname: string): string {
  if (pathname.startsWith('/review')) return 'review';
  if (pathname.startsWith('/mine')) return 'mine';
  if (pathname === '/submit') return 'submit';
  if (pathname === '/hot' || pathname === '/hotweek' || pathname.startsWith('/post/')) return 'hot';
  if (pathname === '/more') return 'more';
  return 'home';
}
