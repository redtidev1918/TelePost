# 管理控制面（实时模型）

本页描述**当前代码与当前生产**中的管理员控制面模型。历史文档如果声称「所有投稿
都进入审核」「失败只显示 failed」，以本页为准。

## 投稿 → 处置（Admission Policy，`SubmissionDisposition`）

处置由**来源可信度**决定，不是入口形式：

| source | 处置 | 含义 |
|---|---|---|
| `api` | 固定 `REVIEW_REQUIRED` | 自动化/第三方入口，始终进入审核群；`API_REVIEW_REQUIRED` 不再关闭 API 的审核（仅兼容保留） |
| `miniapp` | 由 `MINIAPP_REVIEW_REQUIRED` 独立决定 | 真实用户入口；默认 `true`（进入审核），可独立关闭 |
| `chat` / `chat_direct` | 默认 `DIRECT_PUBLISH`，`CHAT_REVIEW_REQUIRED=true` 则 `REVIEW_REQUIRED` | Telegram 私聊投稿，默认直接发布 |

规则实现在 `telepost/domain/submission.py`；运行时覆盖由 `/botconfig` 写入
`runtime-policy.json`。Mini App 与 API **绝不共用开关**。

## 审核（Review）≠ 通知（Notification）

- **Review 是 admission policy**，不是所有 Submission 的必经流程。只有处置为
  `REVIEW_REQUIRED` 的来源才进入审核队列。
- **管理员通知（`NOTIFY_OWNER`）覆盖所有来源**：`chat_direct`、`miniapp`、`api`
  的投稿都会按 logical submission 通知 Owner（直发成功或入队成功各通知一次）。
- 通知策略与审核策略分离，不能因为来源直发就认为没有管理通知。

## Moderation Blocks（追加式治理名单）

治理层使用 `moderation_blocks` 表（append-only deny list，见
`telepost/storage/sqlite/moderation.py`）：

```text
subject     canonical actor identity（user:<id> | api:<id>），非显示字符串
reason      操作原因（≤500 字符，可空）
created_by  操作者（页内整数 ID）
created_at  操作 Unix 时间
```

约束与行为：

- `subject` 唯一；同一 subject 重复封禁幂等（返回已存在的行 id）。
- **禁止物理删除历史治理记录**。当前没有状态列，也没有解封/过期字段；受封来源在接收新投稿
  时被 `is_blocked()` 拦截，历史投稿与审核记录保持不变。
- `removed` / `expired` 生命周期与可见的「解封时间/操作人」为 **planned**，当前代码未实现，
  不要按已能力宣传。
- 封禁入口当前由管理 API/Telegram 命令调用 `add_block()` 写入；操作事件应同时写入
  `audit_events`（审计是 append-only 证据面）。

## Operational Result Contract（错误可诊断）

失败不再只描述成败。TelePost 内部类型化错误与 HTTP 响应携带：

```text
code          稳定错误码（如 recovery_not_configured、review_queue_busy）
stage         失败阶段（如 recovery_request、submission、publish）
retryable     true/false：是否值得原样重试
hint          给操作员的提示文案（绝不吞掉异常）
```

示例：手动恢复失败（`RecoveryError`）会把 `code` / `stage` / `retryable` / `hint`
映射成用户可见的「恢复请求失败 + 原因 + 建议」，而不是只显示动作失败。

### 恢复/重抓已知坑

- 恢复与重抓需要 TelePost 容器内设置 `PIXIVFLOW_REFETCH_BASE_URL` 与
  `PIXIVFLOW_REFETCH_TOKEN`，并且两值必须**与 PixivFlow 端同名 Secret 一致**
  （尤其不要把 `apikey=` 之类的前缀裹进 `PIXIVFLOW_REFETCH_TOKEN`）。
- 曾出现「恢复请求失败」根本原因是 `PIXIVFLOW_REFETCH_BASE_URL` 缺失，而不是恢复逻辑
  错误；已修复并在生产补上 `https://pixivflow-scheduler.fly.dev`。排查顺序：
  1. TelePost 容器内 `env` 确认两个变量都存在、值为纯 token/base URL；
  2. `GET {base}/internal/targets/<target>/recover/<fake-uuid>` 应返回「manual recovery not found」（
     该响应同时证明认证通过、路由可达），否则是变量/Secret 不一致。
