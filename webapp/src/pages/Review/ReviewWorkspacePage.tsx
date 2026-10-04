import { useEffect, useState } from 'react';
import { useLocation } from 'react-router-dom';
import { useBotNavigate } from '../../lib/useBotNavigate';
import { useAuth } from '../../auth/AuthProvider';
import { ReviewQueuePage } from '../ReviewQueue/ReviewQueuePage';
import { ReviewHistoryPage } from '../ReviewHistory/ReviewHistoryPage';
import { PageHeader } from '../../components/ui/PageHeader';
import { Segmented } from '../../components/ui/Segmented';

/**
 * 审核 = ONE reviewer workspace (§review-workspace), not two top-level pages.
 *
 * 审核队列 and 审核历史 keep their own components and tests; this page is the
 * shell that turns them into two tabs of one job: 待处理 / 历史. The reviewer
 * lands on what to DO (待处理) and can flip to what was DONE (历史) without
 * leaving the section — and the bottom bar never grows a 6th tab for it.
 *
 * Admin-only surfaces are reachable from the header overflow (更多), never from
 * an extra tab.
 */
export function ReviewWorkspacePage() {
  const navigate = useBotNavigate();
  const location = useLocation();
  const { isAdmin } = useAuth();
  const initial: 'queue' | 'history' =
    location.pathname === '/review/history' ? 'history' : 'queue';
  const [tab, setTab] = useState<'queue' | 'history'>(initial);

  // Deep links (/review/history) and the tab control are the same state.
  useEffect(() => {
    setTab(location.pathname === '/review/history' ? 'history' : 'queue');
  }, [location.pathname]);

  return (
    <div className="stack">
      <PageHeader
        title="审核"
        subtitle={tab === 'queue' ? '处理等待中的投稿' : '查看已处理的记录'}
        action={
          <button
            type="button"
            className="page-section__action"
            data-testid="review-more"
            onClick={() => navigate('/more')}
          >
            更多
          </button>
        }
      />
      <Segmented<'queue' | 'history'>
        testId="review-tab"
        value={tab}
        options={[
          { value: 'queue', label: '待处理' },
          { value: 'history', label: '历史' },
        ]}
        onChange={(next) => {
          setTab(next);
          navigate(next === 'history' ? '/review/history' : '/review');
        }}
      />
      {tab === 'queue' ? <ReviewQueuePage /> : <ReviewHistoryPage />}
      {isAdmin ? (
        <button
          type="button"
          className="page-section__action"
          data-testid="review-admin-entry"
          onClick={() => navigate('/admin')}
        >
          管理面板
        </button>
      ) : null}
    </div>
  );
}
