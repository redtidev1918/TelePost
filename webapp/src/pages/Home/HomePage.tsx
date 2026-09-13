import { Cell, Section, Spinner } from '@telegram-apps/telegram-ui';
import { useQuery } from '@tanstack/react-query';
import { useNavigate } from 'react-router-dom';
import { useAuth } from '../../auth/AuthProvider';
import { fetchMe } from '../../api/me';

const ROLE_LABELS: Record<string, string> = {
  submitter: '投稿者',
  reviewer: '审核员',
  admin: '管理员',
};

export function HomePage() {
  const { user, isReviewer } = useAuth();
  const navigate = useNavigate();
  const me = useQuery({ queryKey: ['me'], queryFn: fetchMe });

  return (
    <Section header="TelePost">
      {me.isLoading ? (
        <div className="page-loading">
          <Spinner size="m" />
        </div>
      ) : (
        <Cell subtitle={
          (me.data?.name || user?.username || `用户 ${user?.telegram_user_id || ''}`) +
          (user?.roles?.length
            ? ` · ${user.roles.map((r) => ROLE_LABELS[r] || r).join(' / ')}`
            : '')
        }>
          {me.data ? `#${me.data.telegram_user_id}` : 'TelePost'}
        </Cell>
      )}

      <Section header="操作">
        <Cell onClick={() => navigate('/submit')} after="→">
          投稿
        </Cell>
        <Cell onClick={() => navigate('/mine')} after="→">
          我的投稿
        </Cell>
        {isReviewer && (
          <Cell onClick={() => navigate('/review')} after="→">
            审核队列
          </Cell>
        )}
      </Section>
    </Section>
  );
}
