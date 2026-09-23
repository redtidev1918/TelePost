# 命令参考

## 权限

- **用户**：任何未被拉黑的用户。
- **Admin**：`ADMIN_IDS`，`OWNER_ID` 自动包含在内。
- **Owner**：仅 `OWNER_ID`；Token、运行配置、批量删帖和黑名单等敏感操作不向 Admin 扩大。

## 用户命令

| 命令 | 说明 |
|---|---|
| `/start` | 显示欢迎信息和菜单 |
| `/submit` | 开始投稿 |
| `/cancel` | 取消当前投稿；会话外会说明没有进行中的投稿 |
| `/search <关键词>` | 搜索；支持 `#标签`、`-t day\|week\|month`、`-n <数量>` |
| `/tags` | 标签云 |
| `/hot [数量] [week]` | 全部时间热榜；`/hot 20` 查看 TOP 20 |
| `/hotweek [数量]` | 本周热榜（自然周，周一 00:00 起） |
| `/myposts [数量]` | 我的投稿 |
| `/mystats` | 我的投稿统计 |
| `/settings` | 查看当前公开配置 |
| `/help` | 帮助 |

## Owner 命令

| 命令 | 说明 |
|---|---|
| `/debug` | 当前 Bot 与运行配置诊断 |
| `/searchuser <用户ID>` | 查询指定用户投稿 |
| `/delete_posts <ID或范围...>` | 批量软删除，单次最多 50 个 |
| `/stats` | 全局统计：用户数、帖子数、反应总数（仅 Owner） |
| `/blacklist` | 黑名单面板 |
| `/blacklist_add <用户ID> [原因]` | 加入黑名单 |
| `/blacklist_remove <用户ID>` | 移出黑名单 |
| `/blacklist_list` | 查看黑名单 |
| `/gen_token <名称>` | 生成 API Token；明文只显示一次 |

`/tokens` 和 `/revoke_token <编号>` 只查看或吊销当前用户自己的 Token；新 Token 仍只能
由 Owner 通过 `/gen_token` 生成。

## Admin 命令

| 命令 | 说明 |
|---|---|
| `/ban_user <用户ID> [原因]` | 手动封禁用户（按钮失效兜底；匿名投稿用通知中的 ID） |
| `/ban_api <token编号> [原因]` | 手动禁用 API token（按钮失效兜底） |
| `/rebuild_index` | 清空并重建搜索索引 |
| `/sync_index` | 增量同步数据库与索引 |
| `/index_stats` | 查看数据库/索引差异 |
| `/optimize_index` | 合并 Whoosh 索引段 |

审核群的批准、拒绝、剧透切换和 Pixiv 重抓按钮同样要求 Admin。
重抓需要 `PIXIVFLOW_REFETCH_BASE_URL` 和 `PIXIVFLOW_REFETCH_TOKEN`，只重跑原审核稿的 `target_id`；受理后会在审核群确认，新作品仍需人工审核。

### 「🔄 重抓/换一张」是什么

- **重抓 = 换一个候选**：为当前待审核稿找一个新的、这条审核链还没展示过的作品，找到后**替换**进审核队列；不是重新下载同一个作品。
- 没有新候选时，**当前稿件保持不变**，群里会提示；以后有新作品可以**再次点击重抓**。
- 重抓处理中重复点击不会产生并行任务（提示「正在重抓，请稍候」）。
- 重抓成功后，新稿件同样需要人工审核；旧稿被标记为“已被替换”，旧稿件上的按钮不再可用（请在最新审核稿操作）。
- split-worker 拓扑无需 PixivFlow 与 TelePost 同容器：PixivFlow 平时停止，收到重抓请求会自动唤醒，跑完自动再停。

## `/botconfig`（仅 Owner）

| 命令 | 说明 |
|---|---|
| `/botconfig` | 显示面板 |
| `/botconfig channel @频道或-100ID` | 修改投稿频道 |
| `/botconfig review here` | 把当前群设为审核群 |
| `/botconfig review -100ID` | 按 ID 设置审核群 |
| `/botconfig api_review on\|off` | API 审核开关（仅兼容保留；API 投稿固定进入审核） |
| `/botconfig miniapp_review on\|off` | Mini App 审核开关 |
| `/botconfig chat_review on\|off` | 聊天审核开关 |
| `/botconfig show_submitter on\|off` | 频道署名开关 |
| `/botconfig reset` | 删除运行时覆盖，恢复部署配置 |

切换频道、审核群或 reset 前必须处理完待审队列。多 Bot supervisor 只重启当前 Bot；
单 Bot 部署写入策略后需要手工重启。

## 投稿流程命令

| 命令 | 阶段 | 说明 |
|---|---|---|
| `/done_media` | 上传 | 完成上传并打开预览 |
| `/skip_media` | 上传 | 不上传文件，直接打开预览 |

预览页可编辑标签、标题、简介、链接，补充媒体，切换匿名/剧透，然后发布或取消。
标签必填；`MIXED` 默认同时接受媒体和文档。

## 说明

- `/hot` 读取本地数据库；Telegram Bot API 不提供无副作用回读任意频道帖实时浏览数。
- `/search` 与 `/hot` 支持分页；`/myposts` 按消息逐条输出。
- 黑名单会拦截投稿和按钮交互。

