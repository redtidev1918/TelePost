# 故障排查

> 格式：症状 → 原因 → 处置。最后更新：2026-08

## 启动类

**启动即报 `TOKEN 未设置`** → 环境变量/`config.ini` 未配置或名字写错。使用 `TOKEN`（`BOT_TOKEN`/`TELEGRAM_BOT_TOKEN` 为兼容别名）；Fly.io 用 `flyctl secrets set TOKEN=...`。

**配置文件警告 `config.ini 不存在`** → 仅用环境变量也能运行；想用文件则 `cp config.ini.example config.ini` 后填写。

## 收不到消息 / 无响应

- **Webhook 模式收不到更新**：核对 `WEBHOOK_URL` 为有效 HTTPS 域名、`setWebhook` 成功、`WEBHOOK_SECRET_TOKEN` 与反代一致；同一 Token 不能同时被 Polling 和 Webhook 抢占（也会导致"机器人完全无响应"）。
- **确认没有第二个实例**在跑同一 Token（`ps aux | grep main.py` / `docker ps`）。

## Docker 容器反复重启

`HEALTHCHECK` 请求 8080 `/health` 失败即 unhealthy。Polling 模式依赖 `health.py`（已内置）；确认端口映射与 `WEBHOOK_PORT` 一致，日志查 `健康检查` 关键字。

## 搜索问题

- **搜索无结果**：1) `SEARCH_ENABLED` 是否开启；2) 索引与库不同步 → `python3 -m utils.index_manager status` 后 `sync`；3) 时间筛选依赖 `publish_time` 正常。
- **中文搜不到**：`simple` 分词器按整词匹配，切回 `jieba`（需安装）可获得子词匹配。
- **/tags 报错**：超长标签按钮已做安全跳过；若仍报错请检查异常标签数据。

## 统计问题

- **/hot 数据偏旧**：统计任务**每 2 小时**批量执行一次，非实时。
- **/hot 为空**：帖子尚未被统计周期覆盖，或均已被标记删除。

## 投稿问题

- **提示"内容发送失败，数据已保留"**：多为瞬时网络/权限问题；重新 `/submit` 再走一遍（数据保留机制已避免重传媒体）；持续失败检查机器人对频道的发帖权限。
- **触发频率限制**：默认每小时 10 次，`SUBMIT_LIMIT_PER_HOUR` 可调（0 关闭）。
- **会话超时**：`SESSION_TIMEOUT`（默认 900 秒）不活动即清理，重新 `/submit` 即可。

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
最后更新：2026-08
