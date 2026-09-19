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

/**
 * Admin Control Plane panel (§admin-api): one screen, four sections —
 * 运行状态 / 运行策略 / 角色管理 / 黑名单. The server re-verifies admin
 * rights on every request; this page only renders what the verified session
 * may do (§45). Mutations are reversible (re-grant / un-blacklist), so no
 * confirm dialogs — the audit log is the durable record.
 */

function useAdminStatus() {
  return useQuery({
    queryKey: ['admin-status'],
    queryFn: fetchAdminStatus,
    refetchInterval: 30_000,
  });
}

function mutationError(error: unknown): string {
  return (error as Error).message || '操作失败';
}

export function AdminPage() {
  const status = useAdminStatus();

  if (status.isLoading) {
    return (
      <div className="page-loading">
        <Spinner size="m" />
      </div>
    );
  }
  if (status.isError) {
    return <div className="page-error">加载失败：{mutationError(status.error)}</div>;
  }

  const snapshot = status.data as AdminStatusSnapshot;
  return (
    <>
      <StatusSection snapshot={snapshot} />
      <PolicySection embedded={snapshot.policy} restartManaged={snapshot.restart_managed} />
      <RolesSection />
      <BlacklistSection />
    </>
  );
}

function StatusSection({ snapshot }: { snapshot: AdminStatusSnapshot }) {
  const q = snapshot.queue;
  return (
    <Section header="运行状态" data-testid="admin-status">
      <Cell subtitle={`${snapshot.version.version} @ ${snapshot.version.commit.slice(0, 8) || '-'}`}>
        服务版本
      </Cell>
      <Cell subtitle={`${q.pending} 待审 · ${q.published} 已发布 · ${q.rejected} 已拒绝`}>
        审核队列
      </Cell>
      <Cell subtitle={`${q.staging} 准备中 · ${q.failed} 失败 · ${q.superseded} 已更换`}>
        投稿处理
      </Cell>
      <Cell subtitle={`近 24 小时 ${snapshot.submissions_24h} 次投稿`}>活跃度</Cell>
      <Cell
        subtitle={
          snapshot.refetch.active
            ? `${snapshot.refetch.active} 个进行中`
            : '无进行中的候选更换'
        }
      >
        候选更换
      </Cell>
      {snapshot.refetch.recent_failures.length > 0 && (
        <Cell subtitle={snapshot.refetch.recent_failures
          .map((f) => `#${f.source_review_id} ${f.failure_code || f.state}`)
          .join(' · ')}>
          最近更换失败
        </Cell>
      )}
      <Cell subtitle={`${snapshot.blacklist_size} 人`}>黑名单</Cell>
    </Section>
  );
}

/** Server only accepts these toggles from the Mini App API (§admin-api). */
const TOGGLES = [
  { key: 'chat_review' as const, label: '审核群复审', testid: 'admin-policy-chat-review' },
  { key: 'show_submitter' as const, label: '公开投稿人', testid: 'admin-policy-show-submitter' },
];

function PolicySection({
  embedded,
  restartManaged,
}: {
  embedded: AdminStatusSnapshot['policy'];
  restartManaged: boolean;
}) {
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
      header={restartManaged ? '运行策略（保存后服务自动重启生效）' : '运行策略'}
      data-testid="admin-policy"
    >
      {TOGGLES.map((toggle) => (
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
      <Cell subtitle={`API 投稿必须审核（固定开启）`}>API 复审</Cell>
      <Cell subtitle="小程序投稿必须审核（在 Bot 侧调整）">
        小程序复审
      </Cell>
      {error && <div className="mutation-help">{error}</div>}
    </Section>
  );
}

function RolesSection() {
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
    <Section header="角色管理" data-testid="admin-roles">
      {bindings.isLoading && <Cell>加载中…</Cell>}
      {bindings.isError && <Cell>加载失败：{mutationError(bindings.error)}</Cell>}
      {(bindings.data ?? []).length === 0 && !bindings.isLoading && (
        <Cell subtitle="通过 env 引导的管理员不在此列">暂无持久化角色绑定</Cell>
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
              移除
            </Button>
          }
          subtitle={
            `${binding.role} · 授予者 ${binding.created_by || '-'}`
          }
          data-testid="admin-role-item"
        >
          用户 {binding.telegram_user_id}
        </Cell>
      ))}
      <Cell>
        <Input
          inputMode="numeric"
          placeholder="Telegram 用户 ID（数字）"
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
            授予审核员
          </Button>
          <Button
            size="s"
            disabled={!Number.isInteger(parsed) || parsed <= 0 || add.isPending}
            loading={add.isPending && add.variables === 'admin'}
            onClick={() => add.mutate('admin')}
            data-testid="admin-role-add-admin"
          >
            授予管理员
          </Button>
        </div>
      </Cell>
      {error && <div className="mutation-help">{error}</div>}
    </Section>
  );
}

function BlacklistSection() {
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
    <Section header="黑名单" data-testid="admin-blacklist">
      {entries.isLoading && <Cell>加载中…</Cell>}
      {entries.isError && <Cell>加载失败：{mutationError(entries.error)}</Cell>}
      {(entries.data ?? []).length === 0 && !entries.isLoading && (
        <Cell>黑名单为空。</Cell>
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
              移除
            </Button>
          }
          subtitle={entry.reason || '未填写原因'}
          data-testid="admin-blacklist-item"
        >
          用户 {entry.user_id}
        </Cell>
      ))}
      <Cell>
        <Input
          inputMode="numeric"
          placeholder="要拉黑的 Telegram 用户 ID"
          value={userId}
          data-testid="admin-blacklist-add-input"
          onChange={(e) => setUserId(e.target.value)}
        />
      </Cell>
      <Cell>
        <Input
          placeholder="原因（可选）"
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
          加入黑名单
        </Button>
      </Cell>
      {error && <div className="mutation-help">{error}</div>}
    </Section>
  );
}
