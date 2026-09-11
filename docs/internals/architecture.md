# 架构与职责边界

本页描述当前分层。目标：频道投递只有一个核心，Telegram handler 只做协议适配，
领域逻辑不依赖 python-telegram-bot（PTB）与 SQLite。

## 分层

```
handlers/ (PTB adapter 薄层)        utils/api_server.py (HTTP adapter)
   │ chat/review callbacks             │ JSON / multipart
   ▼                                    ▼
telepost.application
   PublicationService / ReviewQueueService / ReviewService
   （幂等、去重、事务编排；只依赖端口 Protocol）
   ▼                         ▲
telepost.domain              telepost.storage.sqlite
   DeliveryRequest/Result,   ReviewRepository / DeliveryLedgerRepository /
   ReviewStatus 状态机,       PublishedPostRepository
   typed errors              （条件 UPDATE、WAL、唯一键）
   ▲
telepost.telegram.delivery
   planner（纯函数）→ executor（Sender 协议）→ sender/gateway（PTB）
   discussion 策略、registry（转发关联）、preparation（压缩/降级）
```

* `domain`：无 I/O，无 PTB 导入。
* `planner`：纯函数，媒体族/相册拆分可单测。
* `executor`：只认识 `Sender` Protocol，输出三态
  `delivered / uncertain / failed`。
* `sender/gateway`：唯一 import PTB 的投递实现。
* `application`：编排投递、落库、幂等账本；传输层通过端口注入。
* `storage`：有界 repository，不做每表 ORM。

## 投递三态（禁止盲目重试）

| 状态 | 含义 | 允许的动作 |
|---|---|---|
| delivered | 收到了与请求项数量一致的消息回执 | 记录 `published_posts` + `delivery_ledger` |
| uncertain | 超时/网络错误/数量不符；Telegram 可能已收 | **不**自动重发；HTTP 回 `retryable_failure`，靠 `GET /deliveries/lookup` 对账 |
| failed | 确定性失败（BadRequest 等）；相册类错误允许降级单发 | 记录失败原因，审核回到 failed 可人工重试 |

相册 `sendMediaGroup` 的网络异常**不会**降级单发（可能已被 Telegram 接受，
降级必然重复）；确定性异常（如 BadRequest）才降级逐条发送。

## 幂等与去重

* 调用方提供 `idempotency_key`（归一化、截断 240）。
* `delivery_ledger.idempotency_key` 唯一：第二次同 key 直接回放首次结果。
* 作品级去重：7 天窗口内 `(target_id, work_type, pixiv_id)` 命中则回
  `duplicate_existing`。
* 同进程并发同 key 由 `PublicationService` 内的 key 锁串行化；跨进程由
  SQLite UNIQUE + WAL 兜底。
* 只有 confirmed delivery 才写账本；uncertain 不污染未来重试。

## 审核状态机

`pending / failed → publishing → published | failed`；
`rejected / expired / deleted` 终态。

* `publishing` 只能由条件 UPDATE 抢到（`WHERE id=? AND status IN
  ('pending','failed') OR (status='publishing' AND ?-updated_at > 过期阈值))`。
* 并发审批只有一个赢家；崩溃留下的陈旧 `publishing` 可按
  `PUBLISHING_STALE_SECONDS` 回收。
* `mark_published/mark_failed` 同样带 `AND status='publishing'` 守卫。

## 讨论组策略

cover 发频道 → 等待频道消息自动转发到关联讨论组（registry 记录
`(channel_id, msg_id) → (discussion_chat_id, discussion_msg_id)`）→
其余内容回复锚点发讨论组。确定性失败回滚后重试一次；uncertain
（超时等）回滚已知消息后直接上抛。转发采集在 **webhook 与 polling 两条
摄入路径**都注册（webhook 在入队前；polling 用 `TypeHandler` group
`-1000`）。

## 适配层契约（不能破坏的 monkeypatch seam）

测试与部署依赖这些名字，保持可替换：

* `handlers.publish.publish_from_files` / `publish_from_file_ids`
* `handlers.publish.deliver_items_to_chat`
* `handlers.review.queue_review_from_files` / `queue_review_from_file_ids`
* `handlers.review.publish_from_file_ids`

HTTP adapter 与 review approval 都经由 `PublicationService`；
chat / HTTP API / 审核审批不存在第二套发布逻辑。

## 启动

数据库 schema 初始化是 readiness 前置；Whoosh 索引初始化/重建、PixivFlow
子进程管理等非关键工作一律在 readiness 之后后台执行，失败可降级，不阻塞
webhook 绑定与健康检查。

## 为什么不抽 telegram-publish-kit

当前**不拆**。投递核心确实已 PTB-free（domain/planner/executor），但：

1. 仓库内只有 TelePost 一个真实消费者，YAGNI；
2. 讨论组策略、file_id 账本、caption 构建仍带 TelePost 业务语义；
3. 包边界还没有跨仓库稳定 API 的发布/版本化需求。

触发条件（同时满足再拆）：出现第二个真实消费者；该消费者不需要
TelePost 的 submission/review/ledger 概念；投递 API 已稳定一个迭代周期。
