import { tr } from "../../lib/i18n";
import { useTranslation } from 'react-i18next';
import { Button, Spinner } from '@telegram-apps/telegram-ui';
import { useInfiniteQuery, useQuery } from '@tanstack/react-query';
import { useBotNavigate } from '../../lib/useBotNavigate';
import { useAuth } from '../../auth/AuthProvider';
import { fetchMe } from '../../api/me';
import { fetchHotPosts } from '../../api/posts';
import { fetchReviewQueue } from '../../api/reviews';
import { PostCard } from '../../components/PostCard';
import { PageHeader } from '../../components/ui/PageHeader';
import { PageSection } from '../../components/ui/PageSection';
import { ActionBar } from '../../components/ui/ActionBar';
import { EmptyState } from '../../components/ui/EmptyState';

/**
 * Home = dashboard, NOT a second Hot page (§home).
 *
 * It answers three questions only: who am I, what can I do, what is worth a
 * look. Anything bulk belongs to 热门 / 我的投稿. The hot preview is strictly
 * optional: if the content API is disabled or fails the section disappears and
 * the submission entrypoints stay fully usable — Home never becomes an error
 * page because of a non-critical block.
 */

const ROLE_LABELS = (): Record<string, string> => ({
  submitter: tr("投稿者"),
  reviewer: tr("审核员"),
  admin: tr("管理员"),
});

const HOT_PREVIEW_LIMIT = 3;

function HotPreview() {
  useTranslation();
  const navigate = useBotNavigate();
  const query = useQuery({
    queryKey: ['hot-preview', 'all'],
    queryFn: () => fetchHotPosts('all'),
    retry: false,
  });
  if (query.isLoading) {
    return (
      <div className="page-loading">
        <Spinner size="s" />
      </div>
    );
  }
  // Optional feature: a 404 / disabled content API must never turn Home into an
  // error page (§home-degrade).
  if (query.isError) return null;
  const items = (query.data?.items ?? []).slice(0, HOT_PREVIEW_LIMIT);
  if (items.length === 0) {
    return <EmptyState title={tr("还没有热门内容")} hint={tr("发布之后，这里会出现频道里最受关注的内容。")} />;
  }
  return (
    <div className="stack">
      {items.map((post) => (
        <PostCard
          key={post.message_id}
          post={post}
          layout="row"
          onClick={() => navigate(`/post/${post.message_id}`)}
        />
      ))}
    </div>
  );
}

/**
 * Reviewer shortcut. It subscribes to the SAME ['review-queue'] cache entry the
 * 审核 workspace uses (identical options, no polling from Home), so showing the
 * pending count costs no extra request.
 */
function ReviewShortcut() {
  useTranslation();
  const navigate = useBotNavigate();
  const query = useInfiniteQuery({
    queryKey: ['review-queue'],
    queryFn: ({ pageParam }) => fetchReviewQueue(pageParam),
    initialPageParam: null as string | null,
    getNextPageParam: (last) => last.next_cursor,
  });
  const count = query.data?.pages[0]?.items.length ?? 0;
  return (
    <Button
      size="m"
      mode="bezeled"
      stretched
      data-testid="home-review"
      onClick={() => navigate('/review')}
    >
      {query.isLoading ? tr("审核") : count > 0 ? tr("待审核 {{p0}} 条", {p0: count}) : tr("暂无待审核")}
    </Button>
  );
}

export function HomePage() {
  useTranslation();
  const { user, isReviewer } = useAuth();
  const navigate = useBotNavigate();
  const me = useQuery({ queryKey: ['me'], queryFn: fetchMe, retry: false });

  const name = me.data?.name || user?.username || (user ? tr("用户 {{p0}}", {p0: user.telegram_user_id}) : 'TelePost');
  const roles = (user?.roles ?? []).map((role) => ROLE_LABELS()[role] || role);

  return (
    <div className="stack">
      <PageHeader title={name} subtitle={roles.length ? roles.join(' / ') : tr("TelePost 小程序")} />

      <PageSection title={tr("开始")}>
        <ActionBar
          primary={
            <Button size="l" stretched data-testid="home-submit" onClick={() => navigate('/submit')}>
              {tr("投稿")}</Button>
          }
          secondary={
            <>
              <Button
                size="m"
                mode="bezeled"
                data-testid="home-mine"
                onClick={() => navigate('/mine')}
              >
                {tr("我的投稿")}</Button>
              {isReviewer ? <ReviewShortcut /> : null}
            </>
          }
        />
      </PageSection>

      <PageSection
        title={tr("热门")}
        action={tr("查看全部")}
        onAction={() => navigate('/hot')}
        testId="home-hot"
      >
        <HotPreview />
      </PageSection>
    </div>
  );
}
