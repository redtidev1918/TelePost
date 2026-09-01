# 命令参考

> 与 `main.py` 中实际注册的处理器一一对应。最后更新：2026-09

## 用户命令

| 命令 | 说明 | 示例 |
|---|---|---|
| `/start` | 欢迎信息 + 主菜单按钮 | |
| `/submit` | 开始投稿（媒体/文档/混合三种模式由 `BOT_MODE` 决定） | |
| `/search <关键词>` | 全文搜索。支持 `#标签` 前缀、`-t day\|week\|month` 时间过滤、`-n <数量>` 限制条数（≤30） | `/search 教程 -t week -n 20` |
| `/tags` | 标签云 TOP 榜（默认前 20） | |
| `/hot [数量] [时间范围]` | 热门排行榜（默认 10，最多 50） | `/hot 20 week` |
| `/myposts [数量]` | 我的投稿列表 | `/myposts 20` |
| `/mystats` | 我的投稿统计 | |
| `/searchuser <用户>` | 按用户查询投稿 | |
| `/help` | 完整帮助 | |
| `/cancel` | 取消当前投稿会话 | |
| `/settings` | 机器人设置 | |

## 管理命令（需 OWNER / `ADMIN_IDS`）

| 命令 | 说明 |
|---|---|
| `/blacklist_add <ID> [原因]` | 加入黑名单 |
| `/blacklist_remove <ID>` | 移出黑名单 |
| `/blacklist_list` | 查看黑名单 |
| `/blacklist` | 黑名单管理面板 |
| `/delete_posts <ID...>` | 批量删帖：支持单 ID 与范围混合（`100-110 150`），单次最多 50 个；执行软删除（频道消息+索引删除，记录保留） |
| `/rebuild_index` | 全量重建搜索索引 |
| `/sync_index` | 增量同步索引与数据库 |
| `/index_stats` | 查看索引统计 |
| `/optimize_index` | 合并索引段（优化） |
| `/debug` | 调试信息 |

## 所有者运行配置（仅 `OWNER_ID`）

| 命令 | 说明 |
|---|---|
| `/botconfig` | 显示当前 Bot 的配置面板 |
| `/botconfig channel @频道或-100ID` | 修改投稿频道；存在 pending 时拒绝切换 |
| `/botconfig review here` | 将当前群设为审核群 |
| `/botconfig review -100ID` | 按 ID 修改审核群；存在 pending 时拒绝切换 |
| `/botconfig api_review on\|off` | API 投稿审核开关 |
| `/botconfig chat_review on\|off` | Telegram 聊天投稿审核开关 |
| `/botconfig show_submitter on\|off` | 频道是否显示投稿人；关闭相当于全部匿名 |
| `/botconfig reset` | 删除运行时覆盖并恢复部署配置 |

`/botconfig` 只允许修改非敏感运行策略；Token、Owner、管理员列表和 Webhook 密钥不开放。
多 Bot supervisor 会只重载当前 Bot，通常约 6 秒恢复。

## 投稿流程内命令（仅会话中有效）

| 命令 | 所处阶段 | 说明 |
|---|---|---|
| `/done_media` | 媒体上传 | 结束媒体上传，进入标签步骤 |
| `/skip_media` | 文档模式的媒体可选步骤 | 跳过媒体上传 |
| `/done_doc` | 文档上传 | 结束文档上传 |
| `/skip_optional` | 链接/标题/简介步骤 | 跳过当前及后续全部可选项 |

## 按钮与回调

- 主菜单 / 搜索时间筛选（今日/本周/本月/全部）/ 标签云标签按钮（超长标签按钮会被安全跳过，避免超过 Telegram callback_data 64 字节上限）/ OWNER 删除确认（yes/no）。
- **发布前预览页**（/submit 流程最后一步）：✅ 确认发布 / 🏷️ 改标签 / 📝 改简介 / 📎 补充媒体 / ❌ 取消投稿。
- **分页导航**：/search 与 /hot 多页结果底部提供 ⬅️ 上一页 / 页码 / 下一页 ➡️。
- 底部菜单文字快捷词自动映射到命令（见 `handlers/command_handlers.py` 的 `handle_menu_shortcuts`）。

## 频率限制

每用户每小时默认最多发起 **10** 次投稿（`SUBMIT_LIMIT_PER_HOUR` 可调，0 关闭），超限收到友好提示。

---
最后更新：2026-09
