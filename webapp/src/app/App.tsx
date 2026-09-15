import { useBotNavigate } from '../lib/useBotNavigate';
/**
 * App shell: Telegram UI chrome + AuthProvider + routes.
 *
 * Layout contract (§layout): one viewport-height column. The route content is
 * the ONLY scrolling region and reserves the fixed BottomNav's measured height,
 * so no route's last element (submit button, last card, action row) can ever be
 * covered. The reserve is measured from the real TelegramUI Tabbar at runtime —
 * never a hard-coded device offset — and TelegramUI/AppRoot owns the platform
 * safe-area insets.
 *
 * Routing (§44): submitter sees Home/Submit/MySubmissions; reviewer sees the
 * ReviewQueue/ReviewDetail additionally. Server-side RBAC remains the
 * authority — the router only hides what the verified roles say (§45).
 */
import { useCallback, useEffect, useRef } from 'react';
import { Route, Routes, useLocation } from 'react-router-dom';
import { initDataStartParam } from '@telegram-apps/sdk';
import { submissionIntent } from '../lib/submissionEntry';
import { Tabbar } from '@telegram-apps/telegram-ui';
import { AuthProvider, useAuth } from '../auth/AuthProvider';
import { HomePage } from '../pages/Home/HomePage';
import { SubmitPage } from '../pages/Submit/SubmitPage';
import { MySubmissionsPage } from '../pages/MySubmissions/MySubmissionsPage';
import { SubmissionDetailPage } from '../pages/SubmissionDetail/SubmissionDetailPage';
import { ReviewEditPage } from '../pages/ReviewEdit/ReviewEditPage';
import { EditorialHistoryPage } from '../pages/EditorialHistory/EditorialHistoryPage';
import { ReviewQueuePage } from '../pages/ReviewQueue/ReviewQueuePage';
import { ReviewDetailPage } from '../pages/ReviewDetail/ReviewDetailPage';

interface NavItem {
  path: string;
  label: string;
  show: (isReviewer: boolean) => boolean;
}

const NAV: NavItem[] = [
  { path: '/', label: '首页', show: () => true },
  { path: '/submit', label: '投稿', show: () => true },
  { path: '/mine', label: '我的投稿', show: () => true },
  { path: '/review', label: '审核队列', show: (r) => r },
];

const ERROR_TEXT: Partial<Record<ReturnType<typeof useAuth>['status'], { title: string; hint?: string }>> = {
  outside_telegram: {
    title: '请在 Telegram 中打开 TelePost 小程序。',
    hint: '浏览器直接访问仅用于调式；生产环境必须从 Telegram 进入。',
  },
  miniapp_disabled: { title: '小程序功能未启用，请稍后再试。' },
  auth_failed: { title: '小程序登录失败，请关闭后重新打开。', hint: '如持续失败，请更新 Telegram 后重试。' },
  server_unavailable: { title: '无法连接服务器，请稍后重试。' },
};

/**
 * Publish the fixed BottomNav's real height as a CSS variable so scrolling
 * content reserves exactly that much space (measured, never guessed).
 */
function useBottomNavReserve() {
  const observerRef = useRef<ResizeObserver | null>(null);
  const listenerRef = useRef<(() => void) | null>(null);
  return useCallback((node: HTMLDivElement | null) => {
    const root = document.documentElement;
    observerRef.current?.disconnect();
    observerRef.current = null;
    if (listenerRef.current) {
      window.removeEventListener('resize', listenerRef.current);
      listenerRef.current = null;
    }
    if (!node) {
      root.style.setProperty('--app-tabbar-reserve', '0px');
      return;
    }
    // TelegramUI's Tabbar renders as a fixed-position child, so the wrapper
    // itself measures 0: measure the real tabbar element inside it.
    const target = (node.firstElementChild as HTMLElement) ?? node;
    const apply = () => {
      const height = Math.ceil(target.getBoundingClientRect().height);
      root.style.setProperty('--app-tabbar-reserve', `${height}px`);
    };
    listenerRef.current = apply;
    apply();
    if (typeof ResizeObserver !== 'undefined') {
      observerRef.current = new ResizeObserver(apply);
      observerRef.current.observe(target);
    }
    window.addEventListener('resize', apply);
  }, []);
}

export function App() {
  return (
    <AuthProvider>
      <Shell />
    </AuthProvider>
  );
}

function Shell() {
  const { status, isReviewer } = useAuth();
  const location = useLocation();
  const navigate = useBotNavigate();
  const navRef = useBottomNavReserve();

  // Submission deep link (§submission-entrypoint): startapp=submit is
  // NAVIGATION INTENT ONLY — it never authenticates. The real identity still
  // comes from server-validated Telegram initData (AuthProvider); this effect
  // only lands an authenticated session on the submit route.
  useEffect(() => {
    if (status !== 'authenticated') return;
    let startParam: string | undefined;
    try {
      startParam = initDataStartParam();
    } catch {
      return; // browser/dev launch without Telegram launch params
    }
    const route = submissionIntent(startParam);
    if (route) {
      navigate(route);
    }
  }, [status, navigate]);

  if (status === 'loading' || status === 'authenticating') {
    // Never flash an error before we positively know it: session boot is
    // in-flight (or launch context is being detected).
    return <div className="page-loading">正在打开 TelePost 小程序…</div>;
  }

  // Only the ABSENCE of Telegram launch data may say "open in Telegram". A
  // server/auth failure inside Telegram shows its own precise message (§27).
  if (status !== 'authenticated') {
    const entry = ERROR_TEXT[status];
    return (
      <div className="page-error">
        {entry?.title ?? '小程序暂不可用，请稍后再试。'}
        {entry?.hint ? <div className="mutation-help">{entry.hint}</div> : null}
      </div>
    );
  }

  const visibleNav = NAV.filter((item) => item.show(isReviewer));

  return (
    <div className="app-safe">
      <main className="page">
        <Routes>
          <Route path="/" element={<HomePage />} />
          <Route path="/submit" element={<SubmitPage />} />
          <Route path="/mine" element={<MySubmissionsPage />} />
          <Route path="/mine/:id" element={<SubmissionDetailPage />} />
          <Route path="/mine/:id/editorial" element={<EditorialHistoryPage />} />
          {isReviewer && <Route path="/review" element={<ReviewQueuePage />} />}
          {isReviewer && (
            <>
              <Route path="/review/:id" element={<ReviewDetailPage />} />
              <Route path="/review/:id/edit" element={<ReviewEditPage />} />
            </>
          )}
        </Routes>
      </main>
      <div ref={navRef} className="bottom-nav">
        <Tabbar data-testid="bottom-nav">
          {visibleNav.map((item) => (
            <Tabbar.Item
              key={item.path}
              text={item.label}
              selected={item.path === '/' ? location.pathname === '/' : location.pathname.startsWith(item.path)}
              onClick={() => navigate(item.path)}
            />
          ))}
        </Tabbar>
      </div>
    </div>
  );
}