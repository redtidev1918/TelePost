import { describe, expect, it } from 'vitest';
import { navigationForSpace, isNavItemActive, MAX_TAB_COUNT } from '../lib/navigation';

describe('Mini App 导航（移动端信息架构）', () => {
  it('普通用户：首页 / 热门 / 投稿 / 我的 / 更多', () => {
    const labels = navigationForSpace(false).map((n) => n.label);
    expect(labels).toEqual(['首页', '热门', '投稿', '我的', '更多']);
    expect(labels).not.toContain('审核队列');
    expect(labels).not.toContain('审核历史');
  });

  it('reviewer：审核取代「更多」，不再出现审核队列/审核历史两个 Tab', () => {
    const labels = navigationForSpace(true).map((n) => n.label);
    expect(labels).toEqual(['首页', '热门', '投稿', '我的', '审核']);
    expect(labels).not.toContain('审核队列');
    expect(labels).not.toContain('审核历史');
  });

  it('admin 也不超过 5 个 Tab，管理不占底部导航位', () => {
    const adminLabels = navigationForSpace(true, true).map((n) => n.label);
    expect(adminLabels).toHaveLength(MAX_TAB_COUNT);
    expect(adminLabels).not.toContain('管理');
    // 只有审核角色的管理员拿到审核工作区；非审核管理员拿到「更多」（内含管理入口）。
    expect(navigationForSpace(false, true).map((n) => n.label)).toContain('更多');
    expect(navigationForSpace(false, true).map((n) => n.label)).not.toContain('管理');
  });

  it('任何角色组合都不会超过 5 项', () => {
    for (const isReviewer of [false, true]) {
      for (const isAdmin of [false, true]) {
        expect(navigationForSpace(isReviewer, isAdmin).length).toBeLessThanOrEqual(MAX_TAB_COUNT);
      }
    }
  });
});

describe('BottomNav selected 状态', () => {
  it('一级页面各自高亮，互不重叠', () => {
    expect(isNavItemActive('/', '/')).toBe(true);
    expect(isNavItemActive('/hot', '/hot')).toBe(true);
    expect(isNavItemActive('/submit', '/submit')).toBe(true);
    expect(isNavItemActive('/mine', '/mine')).toBe(true);
    expect(isNavItemActive('/more', '/more')).toBe(true);
    // 同一个路由只能点亮一个 Tab。
    expect(
      ['/', '/hot', '/submit', '/mine', '/more'].filter((p) => isNavItemActive(p, '/mine')),
    ).toEqual(['/mine']);
  });

  it('深层页面归属于正确的一级 Tab', () => {
    expect(isNavItemActive('/mine', '/mine/42')).toBe(true);
    expect(isNavItemActive('/mine', '/mine/42/editorial')).toBe(true);
    expect(isNavItemActive('/hot', '/post/99')).toBe(true);
    expect(isNavItemActive('/hot', '/hotweek')).toBe(true);
    expect(isNavItemActive('/review', '/review/7')).toBe(true);
    expect(isNavItemActive('/review', '/review/7/edit')).toBe(true);
    expect(isNavItemActive('/review', '/review/history')).toBe(true);
  });

  it('/review/history 不会错误高亮内容页，/mine 详情也不会点亮审核', () => {
    expect(isNavItemActive('/hot', '/review/history')).toBe(false);
    expect(isNavItemActive('/mine', '/review/history')).toBe(false);
    expect(isNavItemActive('/review', '/mine/42')).toBe(false);
  });

  it('管理面板不属于任何一个底部 Tab（它是二级工作区）', () => {
    for (const path of ['/', '/hot', '/submit', '/mine', '/more', '/review']) {
      expect(isNavItemActive(path, '/admin')).toBe(false);
    }
  });
});
