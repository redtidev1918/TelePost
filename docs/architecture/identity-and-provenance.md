# Identity, Actor, Submitter, Source — TelePost 投稿归属契约

> 架构契约（architecture contract），不是事故报告。改任何与投稿归属相关的代码前先读本文。

## 四个正交概念

| 概念 | 是什么 | 存哪里 | 是否产生「我的投稿」归属 |
| --- | --- | --- | --- |
| **Actor**（执行者） | 谁/什么东西执行了这次请求 | `pending_reviews.actor_kind` (`user`\|`service`\|`unknown`) + `actor_subject` | 否 |
| **Submitter**（投稿人） | 稿件在业务上属于哪个 Telegram 用户 | `pending_reviews.submitter_user_id` / `submitter_username`（可空） | **是（唯一）** |
| **Source**（来源/传输） | 稿件从哪个通道产生 | `pending_reviews.source` (`chat`\|`api`\|...) + `target_id`/`source_ref`/`source_label` | 否 |
| **API token 持有者** | 鉴权主体 | `api_tokens.telegram_user_id`（legacy） | 否（绝不允许当 submitter） |

不变式：

```
transport != identity
source != ownership
authentication principal != submitter
```

## Principal 模型

`utils/api_server._resolve_principal` 返回的 principal 现在带 `kind`：

```python
{"kind": "user",    "telegram_user_id": 123, ...}   # Mini App session（ma_v1.*）
{"kind": "service", "telegram_user_id": 绑定值, "token_id": i, ...}  # API token（tp_*）
```

- 只有 `kind=user` 的 principal 会写入 `submitter_user_id`。
- `kind=service` 的 principal 永远写 `submitter_user_id=NULL`，
  `actor_kind='service'`，`actor_subject=f"api_token:{id}"`。
- 请求体里的 `user_id` / `actor_kind` 一律被忽略（服务端只信 principal）。

## 哪些投稿「是我的投稿」

`GET /api/v1/me/submissions` 查询键是 `submitter_user_id = 当前已验证 Telegram 用户`：

- Mini App 人类投稿（transport 是 HTTP，但 actor=user）→ 出现；
- 聊天投稿 + `CHAT_REVIEW_REQUIRED=true`（进入审核队列）→ 出现；
- PixivFlow 定时/API 自动投稿（actor=service，submitter=NULL）→ **永不出现**，
  即使 API token 绑定了管理员；
- 管理员触发自动任务（actor=user/admin，submitter=NULL）→ 不成为管理员投稿；
- 聊天直发（`CHAT_REVIEW_REQUIRED=false`，不进 pending_reviews）→ 不在「我的投稿」。

## Refetch 血缘

替换稿（refetch replacement）由 PixivFlow service 投递，但 ownership 跟随审核链：

- 源 review 是 human-owned（`submitter_user_id` 有值）→ 每个新 generation 继承同一 submitter；
- 源 review 是 service-owned → 所有 replacement 保持 `submitter_user_id=NULL`。

实现位置：`telepost/application/review_queue._reserve_replacement`（插入前从 chain 源拷贝
`user_id/username/submitter_*`），历史行由
`scripts/backfill_submission_attribution.py` 沿 `supersedes_review_id` 回填。

## 历史数据收敛（幂等、带审计）

`scripts/backfill_submission_attribution.py`（dry-run 默认）：

- 确定性 service 标记（`target_id`/`source_ref` 非空、`source_label` 前缀 `PixivFlow`、
  `idempotency_key` 前缀 `pixivflow:`、`refetch_request_id` 非空）→ `submitter=NULL`，
  `actor_kind='service'`；
- `source='chat'` → submitter=该行 `user_id`，`actor_kind='user'`；
- 其余 → `submitter=NULL`，`actor_kind='unknown'`（绝不为未证实归属伪造人类投稿人）；
- 每个变更行写 `submission.attribution_backfill` audit；不确定行另写
  `submission.attribution_unknown`；
- 从不改 review `status`、审核人决定、refetch attempt；从不删行；可重复执行。

## 禁止做的事

- `principal.telegram_user_id → submission.user_id → 我的投稿` 的老链接；
- 用 `if source == "api": hide` 之类的传输层补丁（Mini App 人类投稿也走 HTTP）；
- 让 service 通过 body 冒充 submitter；
- 在浏览器/前端展示任何长期凭据（Bot token / API token / PixivFlow secret / Fly token）。