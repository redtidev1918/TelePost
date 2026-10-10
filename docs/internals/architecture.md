# 架构与职责边界

Telegram 与 HTTP 入口通过应用服务编排投稿、审核和发布。投递核心使用端口隔离
python-telegram-bot（PTB）与 SQLite，处理器负责协议适配。

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
* 作品级去重：7 天窗口内 `(target_id, work_type, work_id)` 命中则回
  `duplicate_existing`；存储列仍用兼容名称 `pixiv_id`，repository 边界映射。
* 同进程并发同 key 由 `PublicationService` 内的 key 锁串行化；跨进程由
  SQLite UNIQUE + WAL 兜底。
* 只有 confirmed delivery 才写账本；uncertain 不污染未来重试。

## 审核状态机

`pending / failed → publishing → published | failed`；
`rejected / expired / deleted / superseded` 为终态。重抓成功后旧代成为 `superseded`，
新代成为链头；旧代上的批准、拒绝、剧透与编辑操作均由后端拒绝。

* `publishing` 只能由条件 UPDATE 抢到（`WHERE id=? AND status IN
  ('pending','failed') OR (status='publishing' AND ?-updated_at > 过期阈值))`。
* 并发审批只有一个赢家；崩溃留下的陈旧 `publishing` 可按
  `PUBLISHING_STALE_SECONDS` 回收。
* `mark_published/mark_failed` 同样带 `AND status='publishing'` 守卫。

## 讨论组策略

频道 root 发首组图片 + 首组文件（图片在前、文件在后）→ 分别等待两个频道消息自动
转发到关联讨论组（registry 记录 `(channel_id, msg_id) →
(discussion_chat_id, discussion_msg_id)`）→ 溢出图片回复图片锚点、溢出文件回复
文件锚点。root 未确认时允许按已确认消息保守处理并重试一次；root 确认后，
overflow 失败保留频道主贴，按可确定部分清理讨论区并提示人工核验，不能整组重跑。
转发采集在 **webhook 与 polling 两条摄入路径**都注册（webhook 在入队前；polling 用
`TypeHandler` group `-1000`）。

## 适配层契约（不能破坏的 monkeypatch seam）

测试与部署依赖这些名字，保持可替换：

* `handlers.publish.publish_from_files` / `publish_from_file_ids`
* `handlers.publish.deliver_items_to_chat`
* `handlers.review.queue_review_from_files` / `queue_review_from_file_ids`
* `handlers.review.publish_from_file_ids`

HTTP adapter 与 review approval 都经由 `PublicationService`；
chat / HTTP API / 审核审批不存在第二套发布逻辑。

## 启动

数据库 schema 初始化、未完成审核修复与重抓作业恢复是 readiness 前置。
Whoosh 索引初始化/重建在后台执行，失败可降级。PixivFlow 使用独立运行单元，
不参与 TelePost 的启动就绪判断。

## Novel TXT Telegraph 预览（可选发布增强）

`PublicationService` 在构建频道 caption **之前**，对最终 Publication Snapshot
（实际发布的 ordered items）做一次可选的 novel-preview enrichment：

```text
Final Publication Snapshot
        ├── authoritative Telegram TXT delivery
        └── optional Telegraph preview (TelePress) → publication_previews
```

- 编排：`telepost/application/novel_preview.py` 的 `NovelPreviewEnricher`
  （幂等 by publication key → `publication_previews`；严格超时；失败/超时/不可用
  只影响「有没有在线阅读链接」，绝不影响 Publication 结果）。
- TelePress 渲染后的正文每页按 UTF-8 JSON 限制为 60 KiB，含导航最多
  64 KiB。全文先渲染，再统一按约 20,000 正文字符与字节预算分页；分页与大小校验由上游负责，TelePost 不复制
  分页实现。预览失败仍不影响频道 TXT 发布，root 按钮只在 URL 成功时出现。
- Provider：`telepost/domain/novel_preview.py` 的 `NovelPreviewPublisher` port；
  `telepost/application/telepress_provider.py` 的 `TelePressNovelPreviewPublisher`
  直接使用 `from telepress import TelegraphPublisher`（正式 Python API）。
- 展示放置 SSOT 是 `telepost/domain/presentation_policy.py`
  （`build_publication_presentation`，§online-reading）：频道 CHANNEL_PUBLICATION
  成功才有 `[ 📖 在线阅读 ]` root 按钮；TXT document 始终作为权威下载 artifact
  发送（§telepress-preview）。



## Rich Novel 富媒体预览（PixivFlow → TelePress → Telegraph）

上游可通过独立 TelePress 服务的 `/publish/rich-novel` 生成 `novel_preview_url`。
TelePost 的 provider 也可从最终发布快照与媒体引用生成预览；渲染、分页和媒体 provider
由 TelePress 负责。具体生产服务与 provider 配置以部署仓库为准。

```text
PixivFlow Novel Artifact (txt / md / images / zip)
        │
        ▼
TelePress /publish/rich-novel → media provider → Telegraph
        │
        ▼
novel_preview_url
        │
        ▼
TelePost channel root  →  [ 📖 在线阅读 ]
```

- `build_caption` + `presentation_policy` 是展示放置 SSOT：凡 publication 数据
  携带可用于导航的 `novel_preview_url`（无论来自上游 PixivFlow 字段还是本侧
  TelePress enrichment），频道 CHANNEL_PUBLICATION 就会在主贴 root 挂
  `[ 📖 在线阅读 ]` 按钮（审核/预览面则仍是 footer 超链接），且只出现一次。
- 富媒体正文与插图顺序由 TelePress/Telegraph 负责；TelePost 不解析 Pixiv Novel
  结构，仍然以 TXT/ZIP document 作为权威下载 artifact。
- 可选的 TelePost 本地 TXT 预览（`NOVEL_PREVIEW_ENABLED`）走 `telepress` Python
  库，与上面这条 HTTP 富媒体链路是两个独立入口，共享同一个 `novel_preview_url`
  展示契约。

## 投递核心的包边界

投递核心暂时保留在 TelePost 仓库内：

1. 仓库内只有 TelePost 一个真实消费者；
2. 讨论组策略、file_id 账本、caption 构建仍带 TelePost 业务语义；
3. 包边界还没有跨仓库稳定 API 的发布/版本化需求。

触发条件（同时满足再拆）：出现第二个真实消费者；该消费者不需要
TelePost 的 submission/review/ledger 概念；投递 API 已稳定一个迭代周期。
