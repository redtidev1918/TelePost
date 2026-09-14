/**
 * App shell: Telegram UI chrome + AuthProvider + routes.
 *
 * Routing (§44): submitter sees Home/Submit/MySubmissions; reviewer sees the
 * ReviewQueue/ReviewDetail additionally. Server-side RBAC remains the
 * authority — the router only hides what the verified roles say (§45).
 */
import { Route, Routes, useLocation, useNavigate } from 'react-router-dom';
import { Spinner, Tabbar } from '@telegram-apps/telegram-ui';
import { AuthProvider, useAuth } from '../auth/AuthProvider';
import { HomePage } from '../pages/Home/HomePage';
import { SubmitPage } from '../pages/Submit/SubmitPage';
import { MySubmissionsPage } from '../pages/MySubmissions/MySubmissionsPage';
import { SubmissionDetailPage } from '../pages/SubmissionDetail/SubmissionDetailPage';
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
  const navigate = useNavigate();

  if (status === 'loading' || status === 'authenticating') {
    return <div className="page-loading"><Spinner size="m" /></div>;
  }

  if (status !== 'authenticated') {
    const messages = {
      outside_telegram: '请在 Telegram 中打开 TelePost 小程序。',
      miniapp_disabled: '小程序功能未启用，请稍后再试。',
      invalid_init_data: '小程序登录失败，请关闭后重新打开。',
      expired_init_data: '小程序登录已过期，请关闭后重新打开。',
      auth_failed: '小程序登录失败，请关闭后重新打开。',
      server_unavailable: '服务暂时不可用，请稍后重试。',
    };
    return (
      <div className="page-error">
        {messages[status]}
        <div className="mutation-help">
          {status === 'outside_telegram'
            ? '浏览器直接访问仅用于调试；生产环境必须从 Telegram 进入。'
            : ''}
        </div>
      </div>
    );
  }

  return (
    <div className="app-safe">
      <main className="page">
        <Routes>
          <Route path="/" element={<HomePage />} />
          <Route path="/submit" element={<SubmitPage />} />
          <Route path="/mine" element={<MySubmissionsPage />} />
          <Route path="/mine/:id" element={<SubmissionDetailPage />} />
          {isReviewer && <Route path="/review" element={<ReviewQueuePage />} />}
          {isReviewer && (
            <Route path="/review/:id" element={<ReviewDetailPage />} />
          )}
        </Routes>
      </main>
      <Tabbar>
        {NAV.filter((item) => item.show(isReviewer)).map((item) => (
          <Tabbar.Item
            key={item.path}
            text={item.label}
            selected={location.pathname.startsWith(item.path)}
            onClick={() => navigate(item.path)}
          />
        ))}
      </Tabbar>
    </div>
  );
}
