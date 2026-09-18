import { describe, expect, it } from 'vitest';
import { navigationForSpace } from '../app/App';

describe('Mini App 用户空间 vs 管理空间', () => {
  it('普通用户只看用户空间（首页/投稿/我的投稿），看不到审核队列', () => {
    const labels = navigationForSpace(false).map((n) => n.label);
    expect(labels).toEqual(['首页', '投稿', '我的投稿']);
    expect(labels).not.toContain('审核队列');
  });

  it('reviewer 只看管理空间（审核队列），看不到用户投稿菜单', () => {
    const labels = navigationForSpace(true).map((n) => n.label);
    // 服务端 RBAC 仍是唯一权威；前端只隐藏验证过的角色看不到的入口。
    expect(labels).toEqual(['审核队列']);
    expect(labels).not.toContain('投稿');
    expect(labels).not.toContain('我的投稿');
  });
});
