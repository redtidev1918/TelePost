# 工程教训（按事故，非 Bug 清单）

## L-1 #208/#209 系列（2026-10-24/25）— 串行消费者 × 外部 I/O × 证据缺失

- 现象：限流期投稿失败死锁；幻影卡；误报「Pixiv 服务器错误」；恢复点击无响应。
- 触发条件：Telegram 429 × deadline 预算缺陷；reconcile 为 0 媒体行造卡；错误字符串
  携带 raw payload 数字；回调应答前同步等远端 HTTP。
- 根因放大链：局部缺陷（截断退避）→ 清理路径销毁证据（清 preview ids）→ 幂等死锁
  （failed 复用无恢复）→ 误归因（正则吃数字）。
- 为什么测试没拦住：预算测试只断言「不超时」不断言「不早重发」；错误分类测试用干净
  字符串不含真实 body 数字；回调测试全 mock 远端即时返回。
- 防复发不变量：retry_after 最低支付；失败即证据；恢复资格=不可达性论证；
  跨边界错误字符串只带结构化字段；**回调先应答后慢作**（本次落地）。
- 对应测试：test_staging_recovery.py / test_review_service.py（no-payload）/
  PixivFlow terminalReason+telepost-compat / 本次 HOL 故障注入。

> Confirmed（各不变量均有红→绿测试与生产部署证据；回调先应答项随本班次落地）。

## L-2 取证方法论（本机环境限制）

- Fly SSH WireGuard 断裂时：`fly status/machines/logs`（HTTPS API）与公网 /health 仍可用，
  足以定位「机器是否被唤醒」级事实；机内 DB 断言标记 BLOCKED，不猜。
- 「用户无响应」类问题先问三层：更新到达了吗（webhook 访问日志）→ 消费了吗（Inbox 行）
  → 应答了吗（回调窗口）。缺哪层证据就先补哪层的仪表，不归因。
