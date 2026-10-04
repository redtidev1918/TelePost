import { Button } from '@telegram-apps/telegram-ui';
import { useBotNavigate } from '../../lib/useBotNavigate';
import { useAuth } from '../../auth/AuthProvider';
import { PageHeader } from '../../components/ui/PageHeader';
import { PageSection } from '../../components/ui/PageSection';

/**
 * 更多 (§ia): the 5th bottom slot for sessions without a reviewer workspace.
 *
 * It is the account + secondary-surface hub, so the bottom bar never needs a
 * 6th tab: admin-only surfaces (管理) and reviewer surfaces live here instead.
 * Server-side RBAC stays the authority — entries are only OFFERED here, every
 * page still re-verifies the session.
 */

const ROLE_LABELS: Record<string, string> = {
  submitter: '投稿者',
  reviewer: '审核员',
  admin: '管理员',
};

export function MorePage() {
  const navigate = useBotNavigate();
  const { user, isReviewer, isAdmin } = useAuth();
  const roles = (user?.roles ?? []).map((role) => ROLE_LABELS[role] || role);

  return (
    <div className="stack">
      <PageHeader title="更多" />

      <PageSection title="账号">
        <div className="card">
          <div className="card__row card__row--static">
            <div className="card__row-title">
              {user?.username ? `@${user.username}` : `用户 ${user?.telegram_user_id ?? ''}`}
            </div>
            <div className="card__row-meta">
              {roles.length ? roles.join(' / ') : '尚未分配额外角色'}
            </div>
          </div>
        </div>
      </PageSection>

      <PageSection title="快捷入口">
        <div className="card">
          <div
            className="card__row"
            role="button"
            tabIndex={0}
            data-testid="more-submit"
            onClick={() => navigate('/submit')}
            onKeyDown={(event) => {
              if (event.key === 'Enter' || event.key === ' ') {
                event.preventDefault();
                navigate('/submit');
              }
            }}
          >
            <div className="card__row-title">投稿</div>
            <div className="card__row-meta">添加附件并提交审核</div>
          </div>
          <div
            className="card__row"
            role="button"
            tabIndex={0}
            data-testid="more-mine"
            onClick={() => navigate('/mine')}
            onKeyDown={(event) => {
              if (event.key === 'Enter' || event.key === ' ') {
                event.preventDefault();
                navigate('/mine');
              }
            }}
          >
            <div className="card__row-title">我的投稿</div>
            <div className="card__row-meta">查看进度、结果和编辑记录</div>
          </div>
          <div
            className="card__row"
            role="button"
            tabIndex={0}
            data-testid="more-hot"
            onClick={() => navigate('/hot')}
            onKeyDown={(event) => {
              if (event.key === 'Enter' || event.key === ' ') {
                event.preventDefault();
                navigate('/hot');
              }
            }}
          >
            <div className="card__row-title">热门</div>
            <div className="card__row-meta">浏览频道里最受关注的内容</div>
          </div>
        </div>
      </PageSection>

      {isReviewer && (
        <PageSection title="审核">
          <div className="card">
            <div
              className="card__row"
              role="button"
              tabIndex={0}
              data-testid="more-review"
              onClick={() => navigate('/review')}
              onKeyDown={(event) => {
                if (event.key === 'Enter' || event.key === ' ') {
                  event.preventDefault();
                  navigate('/review');
                }
              }}
            >
              <div className="card__row-title">审核工作区</div>
              <div className="card__row-meta">待处理与审核历史</div>
            </div>
          </div>
        </PageSection>
      )}

      {isAdmin && (
        <PageSection title="管理">
          <Button
            mode="outline"
            stretched
            data-testid="more-admin"
            onClick={() => navigate('/admin')}
          >
            管理面板
          </Button>
        </PageSection>
      )}

      <PageSection title="关于">
        <div className="card">
          <div className="card__row card__row--static">
            <div className="mutation-help">
              TelePost Mini App：投稿、查看进度、审核与发布都在同一个 Telegram 小程序里完成。
            </div>
          </div>
        </div>
      </PageSection>
    </div>
  );
}
