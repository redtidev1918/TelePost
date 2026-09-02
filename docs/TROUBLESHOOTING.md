# 故障排查

> 格式：症状 → 原因 → 处置。最后更新：2026-09

## 启动类

**启动即报 `TOKEN 未设置`** → 环境变量/`config.ini` 未配置或名字写错。使用 `TOKEN`（`BOT_TOKEN`/`TELEGRAM_BOT_TOKEN` 为兼容别名）；Fly.io 用 `flyctl secrets set TOKEN=...`。

**配置文件警告 `config.ini 不存在`** → 仅用环境变量也能运行；想用文件则 `cp config.ini.example config.ini` 后填写。

## 收不到消息 / 无响应

- **Webhook 模式收不到更新**：核对 `WEBHOOK_URL` 为有效 HTTPS 域名、`setWebhook` 成功、`WEBHOOK_SECRET_TOKEN` 与反代一致；同一 Token 不能同时被 Polling 和 Webhook 抢占（也会导致"机器人完全无响应"）。
- **确认没有第二个实例**在跑同一 Token（`ps aux | grep main.py` / `docker ps`）。

## Docker 容器反复重启

`HEALTHCHECK` 请求 8080 `/health` 失败即 unhealthy。Polling 模式由
`utils/polling_server.py` 同时承载健康检查和投稿 API；确认端口映射，并在日志中查
`Polling HTTP server started`。多 Bot 模式下父路由固定占 8080，子进程使用
8081/8082/…。

## 搜索问题

- **搜索无结果**：1) `SEARCH_ENABLED` 是否开启；2) 索引与库不同步 → `python3 -m utils.index_manager status` 后 `sync`；3) 时间筛选依赖 `publish_time` 正常。
- **中文搜不到**：`simple` 分词器按整词匹配，切回 `jieba`（需安装）可获得子词匹配。
- **/tags 报错**：超长标签按钮已做安全跳过；若仍报错请检查异常标签数据。

## 统计问题

- **/hot 数据不变**：Telegram Bot API 无法无副作用回读任意频道帖统计，刷新只会重读本地数据。
- **/hot 为空**：数据库没有既有统计，或帖子均已被标记删除。

## 投稿问题

- **提示"内容发送失败，数据已保留"**：多为瞬时网络/权限问题；重新 `/submit` 再走一遍（数据保留机制已避免重传媒体）；持续失败检查机器人对频道的发帖权限。
- **触发频率限制**：默认每小时 10 次，`SUBMIT_LIMIT_PER_HOUR` 可调（0 关闭）。
- **会话超时**：`SESSION_TIMEOUT`（默认 900 秒）不活动即清理，重新 `/submit` 即可。
- **点「📝 开始投稿」按钮后发媒体无响应**：旧版按钮路径只建 DB 会话、不进状态机（v2.10.30 已修：按钮与 `/submit` 共用同一状态机入口）。若仍在旧版，请升级；会话外发媒体现在会收到明确提示「请先发送 /submit」。
- **发媒体后先「✅ 已接收」紧接着「❌ 会话已过期」**：多为状态机在 v2.10.32 前因 `persistent=True` 且无 persistence 从未注册成功（`main.py` 吞掉了注册异常）。升级到 v2.10.32+；日志埋点（`validate_state`/`end_conversation_with_message` 带 user_id）可精确定位来源。
- **审核群预览相册偶发 `Timed out` / `Retry after Ns`（flood）**：控制消息与预览相册已走 `_send_preview_throttled`（RetryAfter 感知 + 指数退避）。批量投稿并发涌入时仍可能短暂限流，按幂等键重试即可；无需人工干预。
- **启动即报 `REVIEW_CHAT_ID 不能等于 CHANNEL_ID`**：审核预览/控制消息/PixivFlow 通知会以"散帖/回复"混进频道。把审核群设成独立的私密群（`/botconfig review here` 或环境变量）。

## 删除与黑名单

- **删帖后想恢复**：软删除仅置 `is_deleted=1`，改回 0 后 `/sync_index`（详见 [internals/moderation](internals/moderation.md)）。
- **黑名单**：拦截投稿与按钮交互；管理命令仅限 OWNER/ADMIN。

## 诊断工具速查

```bash
python3 check_config.py                     # 配置自检
python3 diagnose_stats.py                   # 统计诊断
python3 -m utils.index_manager status       # 索引状态
curl localhost:8080/health                  # 进程存活
```

---
最后更新：2026-09
