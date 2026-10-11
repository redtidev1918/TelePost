import { tr } from "../../lib/i18n";
import { useTranslation } from 'react-i18next';
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

const ROLE_LABELS = (): Record<string, string> => ({
  submitter: tr("投稿者"),
  reviewer: tr("审核员"),
  admin: tr("管理员"),
});

export function MorePage() {
  useTranslation();
  const navigate = useBotNavigate();
  const { user, isReviewer, isAdmin } = useAuth();
  const roles = (user?.roles ?? []).map((role) => ROLE_LABELS()[role] || role);

  return (
    <div className="stack">
      <PageHeader title={tr("更多")} />

      <PageSection title={tr("账号")}>
        <div className="card">
          <div className="card__row card__row--static">
            <div className="card__row-title">
              {user?.username ? `@${user.username}` : tr("用户 {{p0}}", {p0: user?.telegram_user_id ?? ''})}
            </div>
            <div className="card__row-meta">
              {roles.length ? roles.join(' / ') : tr("尚未分配额外角色")}
            </div>
          </div>
        </div>
      </PageSection>

      <PageSection title={tr("快捷入口")}>
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
            <div className="card__row-title">{tr("投稿")}</div>
            <div className="card__row-meta">{tr("添加附件并提交审核")}</div>
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
            <div className="card__row-title">{tr("我的投稿")}</div>
            <div className="card__row-meta">{tr("查看进度、结果和编辑记录")}</div>
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
            <div className="card__row-title">{tr("热门")}</div>
            <div className="card__row-meta">{tr("浏览频道里最受关注的内容")}</div>
          </div>
        </div>
      </PageSection>

      {isReviewer && (
        <PageSection title={tr("审核")}>
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
              <div className="card__row-title">{tr("审核工作区")}</div>
              <div className="card__row-meta">{tr("待处理与审核历史")}</div>
            </div>
          </div>
        </PageSection>
      )}

      {isAdmin && (
        <PageSection title={tr("管理")}>
          <Button
            mode="outline"
            stretched
            data-testid="more-admin"
            onClick={() => navigate('/admin')}
          >
            {tr("管理面板")}</Button>
        </PageSection>
      )}

      <PageSection title={tr("关于")}>
        <div className="card">
          <div className="card__row card__row--static">
            <div className="mutation-help">
              {tr("TelePost Mini App：投稿、查看进度、审核与发布都在同一个 Telegram 小程序里完成。")}</div>
          </div>
        </div>
      </PageSection>
    </div>
  );
}
