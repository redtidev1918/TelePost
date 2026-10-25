# 决策记录（ADR-lite）

> 只有验证并采纳的才标 Accepted。完整跨仓架构决策在 deploy 仓 ecosystem-platform.md。

## D-2026-10-25-01 Staging 预算与受控恢复（Accepted）

- 背景：#209 事故（限流耗尽 deadline、失败行死锁幂等键、幻影卡）。
- 决策：retry_after 足额支付 + 预算外立即终止；失败保留 preview ids；同键重试经
  `claim_failed_recovery` 单写者 CAS 恢复（仅「从未可操作」行）；reconcile 不造 0 媒体卡。
- 备选：延长 deadline（掩盖）、删行重建（丢身份）、无条件 failed→preparing（丢安全）。
- 依据/测试：tests/test_staging_recovery.py 14 项；v2.84.5 已部署。
- 失效条件：若恢复的私有审核群重复预览被证明不可接受，需改为人工确认恢复。

## D-2026-10-25-02 无 payload 行拒绝发布（Accepted）

- 背景：幻影卡点击空转发布循环（delivery returned no messages）。
- 决策：`approve` 认领后对「零媒体+零文档+零链」行立即释放认领并明确拒绝。
- 依据：API 准入强制 media/documents 至少一项；pending 行必有 staged payload。
- 测试：test_review_service.py 2 项新增（PR #352，`3b71b30`）。

## D-2026-10-25-03 恢复回调 answer-first + 后台持久化提交（Accepted）

- 背景：恢复回调在串行 inbox worker 内应答前等待 ≤120s 远端 HTTP；PixivFlow 冷启动 ~8s；
  应答晚于 Telegram ~10s 窗口 → 「无响应」。
- 决策：本地 durable attempt 行（既有表）先落库 → 立即应答「已受理」→ 远端 POST 由后台
  任务完成（有界重试；300s 周期任务按 requestId 幂等重投 'requested' 行）。
- 备选：缩短 HTTP 超时（仍占 worker）；inbox 并发化（破坏会话顺序语义，拒绝）；
  新建队列框架（违反复用原则，拒绝）。
- 代价：接受语义从「已提交」变为「已受理，后台提交中」；极端情况下用户稍后收到
  失败通知而非即时弹窗。
- 失效条件：若 PixivFlow 恢复 API 长期不可用，attempt 行由 sweep 超龄终态化
  （`submit_retry_exhausted`，15 分钟上限，带审计）。
- 落地：`admit_recovery`/`submit_recovery`/`resubmit_pending_recoveries`（300s 周期）；
  测试 `tests/test_recovery_answer_first.py`（含 HOL 注入：慢远端不阻塞应答）。

## D-2026-10-25-04 inbox 串行模型保留（Accepted）

- 背景：用户怀疑 Durable Inbox 是回归根因。
- 决策：保留严格串行（会话顺序、重放安全是其存在意义）；把**慢 I/O 移出关键路径**，
  而不是并发化消费者。回归定性为 C（暴露既有缺陷），非 A（设计错误）——#209 链在
  HTTP 路径与 inbox 无关；串行语义与 inbox 前 PTB 默认一致。
- 失效条件：若未来单 Bot 更新吞吐成为瓶颈，再评估 per-chat 分片串行。
