import { describe, expect, it } from 'vitest';
import { navigationForSpace } from '../app/App';

describe('Mini App 导航', () => {
  it('普通用户只看用户空间（首页/投稿/我的投稿），看不到审核队列', () => {
    const labels = navigationForSpace(false).map((n) => n.label);
    expect(labels).toEqual(['首页', '热门', '投稿', '我的投稿']);
    expect(labels).not.toContain('审核队列');
  });

  it('reviewer/admin 拥有完整用户功能，并额外看到审核队列', () => {
    const labels = navigationForSpace(true).map((n) => n.label);
    expect(labels).toEqual(['首页', '热门', '投稿', '我的投稿', '审核队列']);
  });

  it('管理员额外看到管理面板入口；非管理员看不到', () => {
    const adminLabels = navigationForSpace(true, true).map((n) => n.label);
    expect(adminLabels).toEqual(['首页', '热门', '投稿', '我的投稿', '审核队列', '管理']);
    expect(navigationForSpace(false, true).map((n) => n.label)).toContain('管理');
    expect(navigationForSpace(false).map((n) => n.label)).not.toContain('管理');
  });
});
