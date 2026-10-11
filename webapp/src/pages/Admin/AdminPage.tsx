import { tr } from "../../lib/i18n";
import { useTranslation } from 'react-i18next';
import { useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { Button, Cell, Input, Section, Spinner, Switch } from '@telegram-apps/telegram-ui';
import {
  addBlacklistEntry,
  addRoleBinding,
  AdminStatusSnapshot,
  BlacklistEntry,
  fetchAdminStatus,
  fetchBlacklist,
  fetchRoleBindings,
  removeBlacklistEntry,
  removeRoleBinding,
  RoleBinding,
} from '../../api/admin';
import { useBackButton } from '../../lib/useBackButton';
import { PageHeader } from '../../components/ui/PageHeader';
import { EmptyState } from '../../components/ui/EmptyState';

/**
 * Admin Control Plane panel (§admin-api): one screen, four sections —
 * 运行状态 / 运行策略 / 角色管理 / 黑名单. The server re-verifies admin
 * rights on every request; this page only renders what the verified session
 * may do (§45). Mutations are reversible (re-grant / un-blacklist), so no
 * confirm dialogs — the audit log is the durable record.
 *
 * It is a SECONDARY workspace: reachable from 更多 / the 审核 header, never
 * from a dedicated bottom tab, and its only back affordance is the Telegram
 * BackButton.
 */

function useAdminStatus() {
  return useQuery({
    queryKey: ['admin-status'],
    queryFn: fetchAdminStatus,
    refetchInterval: 30_000,
  });
}

function mutationError(error: unknown): string {
  return (error as Error).message || tr("操作失败");
}

export function AdminPage() {
  useTranslation();
  const status = useAdminStatus();
  useBackButton('/more');

  if (status.isLoading) {
    return (
      <div className="page-loading">
        <Spinner size="m" />
      </div>
    );
  }
  if (status.isError) {
    return (
      <div className="stack">
        <PageHeader title={tr("管理")} />
        <EmptyState title={tr("加载失败")} hint={mutationError(status.error)} />
      </div>
    );
  }

  const snapshot = status.data as AdminStatusSnapshot;
  return (
    <div className="stack">
      <PageHeader title={tr("管理")} subtitle={tr("运行状态、审核策略、角色与黑名单。")} />
      <StatusSection snapshot={snapshot} />
      <PolicySection embedded={snapshot.policy} restartManaged={snapshot.restart_managed} />
      <RolesSection />
      <BlacklistSection />
    </div>
  );
}

function StatusSection({ snapshot }: { snapshot: AdminStatusSnapshot }) {
  useTranslation();
  const q = snapshot.queue;
  return (
    <Section header={tr("运行状态")} data-testid="admin-status">
      <Cell subtitle={`${snapshot.version.version} @ ${snapshot.version.commit.slice(0, 8) || '-'}`}>
        {tr("服务版本")}</Cell>
      <Cell subtitle={tr("{{p0}} 待审 · {{p1}} 已发布 · {{p2}} 已拒绝", {p0: q.pending, p1: q.published, p2: q.rejected})}>
        {tr("审核队列")}</Cell>
      <Cell subtitle={tr("{{p0}} 准备中 · {{p1}} 失败 · {{p2}} 已更换", {p0: q.staging, p1: q.failed, p2: q.superseded})}>
        {tr("投稿处理")}</Cell>
      <Cell subtitle={tr("近 24 小时 {{p0}} 次投稿", {p0: snapshot.submissions_24h})}>{tr("活跃度")}</Cell>
      <Cell
        subtitle={
          snapshot.refetch.active
            ? tr("{{p0}} 个进行中", {p0: snapshot.refetch.active})
            : tr("没有进行中的重抓/换图")
        }
      >
        {tr("重抓/换图")}</Cell>
      {snapshot.refetch.recent_failures.length > 0 && (
        <Cell subtitle={snapshot.refetch.recent_failures
          .map((f) => `#${f.source_review_id} ${f.failure_code || f.state}`)
          .join(' · ')}>
          {tr("最近重抓失败")}</Cell>
      )}
      <Cell subtitle={tr("{{p0}} 人", {p0: snapshot.blacklist_size})}>{tr("黑名单")}</Cell>
    </Section>
  );
}

/** Server only accepts these toggles from the Mini App API (§admin-api). */
const TOGGLES = () => ([
  { key: 'chat_review' as const, label: tr("TG 聊天投稿先审核"), testid: 'admin-policy-chat-review' },
  { key: 'show_submitter' as const, label: tr("公开投稿人"), testid: 'admin-policy-show-submitter' },
]);

function PolicySection({
  embedded,
  restartManaged,
}: {
  embedded: AdminStatusSnapshot['policy'];
  restartManaged: boolean;
}) {
  useTranslation();
  const queryClient = useQueryClient();
  const [error, setError] = useState('');

  const patch = useMutation({
    mutationFn: (changes: { chat_review?: 'on' | 'off'; show_submitter?: 'on' | 'off' }) =>
      import('../../api/admin').then((m) => m.patchAdminPolicy(changes)),
    onSuccess: () => {
      setError('');
      void queryClient.invalidateQueries({ queryKey: ['admin-status'] });
    },
    onError: (err) => setError(mutationError(err)),
  });

  const valueFor = (key: 'chat_review' | 'show_submitter') =>
    key === 'chat_review' ? embedded.chat_review_required : embedded.show_submitter;

  return (
    <Section
      header={restartManaged ? tr("运行策略（保存后服务自动重启生效）") : tr("运行策略")}
      data-testid="admin-policy"
    >
      {TOGGLES().map((toggle) => (
        <Cell
          key={toggle.key}
          after={
            <Switch
              checked={valueFor(toggle.key)}
              disabled={patch.isPending}
              onChange={(e) => {
                const next = (e.target as HTMLInputElement).checked ? 'on' : 'off';
                patch.mutate({ [toggle.key]: next });
              }}
            />
          }
          data-testid={toggle.testid}
        >
          {toggle.label}
        </Cell>
      ))}
      <Cell subtitle={tr("API 投稿必须审核（固定开启）")}>{tr("API 投稿先审核")}</Cell>
      <Cell subtitle={tr("Mini App 投稿必须审核（在 Bot 侧调整）")}>
        {tr("Mini App 投稿先审核")}</Cell>
      {error && <div className="mutation-help">{error}</div>}
    </Section>
  );
}

function RolesSection() {
  useTranslation();
  const queryClient = useQueryClient();
  const [userId, setUserId] = useState('');
  const [error, setError] = useState('');

  const bindings = useQuery({ queryKey: ['admin-roles'], queryFn: fetchRoleBindings });

  const invalidate = () => {
    void queryClient.invalidateQueries({ queryKey: ['admin-roles'] });
    void queryClient.invalidateQueries({ queryKey: ['admin-status'] });
  };
  const add = useMutation({
    mutationFn: (role: 'reviewer' | 'admin') =>
      addRoleBinding(Number(userId), role),
    onSuccess: () => {
      setError('');
      setUserId('');
      invalidate();
    },
    onError: (err) => setError(mutationError(err)),
  });
  const remove = useMutation({
    mutationFn: (binding: RoleBinding) => removeRoleBinding(binding.telegram_user_id, binding.role),
    onSuccess: () => {
      setError('');
      invalidate();
    },
    onError: (err) => setError(mutationError(err)),
  });

  const parsed = Number(userId);

  return (
    <Section header={tr("角色管理")} data-testid="admin-roles">
      {bindings.isLoading && <Cell>{tr("加载中…")}</Cell>}
      {bindings.isError && <Cell>{tr("加载失败：")}{mutationError(bindings.error)}</Cell>}
      {(bindings.data ?? []).length === 0 && !bindings.isLoading && (
        <Cell subtitle={tr("通过 env 引导的管理员不在此列")}>{tr("暂无持久化角色绑定")}</Cell>
      )}
      {(bindings.data ?? []).map((binding) => (
        <Cell
          key={`${binding.telegram_user_id}:${binding.role}`}
          after={
            <Button
              size="s"
              mode="plain"
              loading={remove.isPending && remove.variables?.telegram_user_id === binding.telegram_user_id}
              onClick={() => remove.mutate(binding)}
            >
              {tr("移除")}</Button>
          }
          subtitle={
            tr("{{p0}} · 授予者 {{p1}}", {p0: binding.role, p1: binding.created_by || '-'})
          }
          data-testid="admin-role-item"
        >
          {tr("用户 ")}{binding.telegram_user_id}
        </Cell>
      ))}
      <Cell>
        <Input
          inputMode="numeric"
          placeholder={tr("Telegram 用户 ID（数字）")}
          value={userId}
          data-testid="admin-role-add-input"
          onChange={(e) => setUserId(e.target.value)}
        />
      </Cell>
      <Cell>
        <div style={{ display: 'flex', gap: 8 }}>
          <Button
            size="s"
            disabled={!Number.isInteger(parsed) || parsed <= 0 || add.isPending}
            loading={add.isPending && add.variables === 'reviewer'}
            onClick={() => add.mutate('reviewer')}
            data-testid="admin-role-add-reviewer"
          >
            {tr("授予审核员")}</Button>
          <Button
            size="s"
            disabled={!Number.isInteger(parsed) || parsed <= 0 || add.isPending}
            loading={add.isPending && add.variables === 'admin'}
            onClick={() => add.mutate('admin')}
            data-testid="admin-role-add-admin"
          >
            {tr("授予管理员")}</Button>
        </div>
      </Cell>
      {error && <div className="mutation-help">{error}</div>}
    </Section>
  );
}

function BlacklistSection() {
  useTranslation();
  const queryClient = useQueryClient();
  const [userId, setUserId] = useState('');
  const [reason, setReason] = useState('');
  const [error, setError] = useState('');

  const entries = useQuery({ queryKey: ['admin-blacklist'], queryFn: fetchBlacklist });

  const invalidate = () => {
    void queryClient.invalidateQueries({ queryKey: ['admin-blacklist'] });
    void queryClient.invalidateQueries({ queryKey: ['admin-status'] });
  };
  const add = useMutation({
    mutationFn: () => addBlacklistEntry(Number(userId), reason),
    onSuccess: () => {
      setError('');
      setUserId('');
      setReason('');
      invalidate();
    },
    onError: (err) => setError(mutationError(err)),
  });
  const remove = useMutation({
    mutationFn: (entry: BlacklistEntry) => removeBlacklistEntry(entry.user_id),
    onSuccess: () => {
      setError('');
      invalidate();
    },
    onError: (err) => setError(mutationError(err)),
  });

  const parsed = Number(userId);
  const canAdd = Number.isInteger(parsed) && parsed > 0 && add.isPending === false;

  return (
    <Section header={tr("黑名单")} data-testid="admin-blacklist">
      {entries.isLoading && <Cell>{tr("加载中…")}</Cell>}
      {entries.isError && <Cell>{tr("加载失败：")}{mutationError(entries.error)}</Cell>}
      {(entries.data ?? []).length === 0 && !entries.isLoading && (
        <Cell>{tr("黑名单为空。")}</Cell>
      )}
      {(entries.data ?? []).map((entry) => (
        <Cell
          key={entry.user_id}
          after={
            <Button
              size="s"
              mode="plain"
              loading={remove.isPending && remove.variables?.user_id === entry.user_id}
              onClick={() => remove.mutate(entry)}
            >
              {tr("移除")}</Button>
          }
          subtitle={entry.reason || tr("未填写原因")}
          data-testid="admin-blacklist-item"
        >
          {tr("用户 ")}{entry.user_id}
        </Cell>
      ))}
      <Cell>
        <Input
          inputMode="numeric"
          placeholder={tr("要拉黑的 Telegram 用户 ID")}
          value={userId}
          data-testid="admin-blacklist-add-input"
          onChange={(e) => setUserId(e.target.value)}
        />
      </Cell>
      <Cell>
        <Input
          placeholder={tr("原因（可选）")}
          value={reason}
          data-testid="admin-blacklist-add-reason"
          onChange={(e) => setReason(e.target.value)}
        />
      </Cell>
      <Cell>
        <Button
          size="s"
          disabled={!canAdd}
          onClick={() => add.mutate()}
          data-testid="admin-blacklist-add-button"
        >
          {tr("加入黑名单")}</Button>
      </Cell>
      {error && <div className="mutation-help">{error}</div>}
    </Section>
  );
}
