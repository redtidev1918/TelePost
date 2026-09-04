# 性能与容量

## 推荐档位

| 场景 | 内存 | 关键配置 |
|---|---:|---|
| 单 Bot、simple 搜索 | 256 MiB | `SEARCH_ANALYZER=simple`、`DB_CACHE_KB=1024` |
| 单 Bot、jieba 搜索 | 512 MiB | 默认 `jieba`、`DB_CACHE_KB=4096` |
| 双 Bot TelePost | 512 MiB | 低配时 `SEARCH_ENABLED=false`、`DB_CACHE_KB=1024` |
| 独立 PixivFlow scheduler | 256 MiB | `download.concurrency=1`、Node heap 96 MiB |

双 Bot + PixivFlow 全部放进一台 512 MiB Machine 虽可在严格限制下运行，但下载和投稿
峰值容易碰到 OOM，而且为了 Cron 必须常驻。Fly.io 推荐拆成 PixivFlow 256 MiB 常驻、
TelePost 512 MiB 自动休眠。

## 主要内存来源

- 每个 Bot 是独立 Python 进程；多 Bot 另有父路由进程。
- `jieba` 词典与 Whoosh 索引明显增加常驻内存。
- SQLite cache 近似受 `DB_CACHE_KB` 控制。
- API 请求是流式传输，但并发上传、Telegram 重发和预览仍会形成峰值。
- PixivFlow 的 Node/V8 基线与下载解码峰值不能靠 Python 配置消除。

不要用 `TIMEOUT`、标签数量等业务参数“优化内存”；收益不可测，反而改变行为。

## 上传与磁盘

- API：最多 50 个文件，单文件 50 MiB，累计 500 MiB。
- 父路由与子服务使用 64 KiB 分块，不整体缓存请求体。
- 临时上传目录正常结束即删除；异常中断后按
  `UPLOAD_SESSION_MAX_AGE_SECONDS`（默认 3600）清扫。
- PixivFlow cache 与 delivery outbox 必须留在持久卷；outbox 未完成时不能删素材。

1 GiB Volume 接收 500 MiB 单请求前要预留数据库、WAL、outbox 和快照之外的足够空间。
高频大投稿应提高 Volume 容量，而不是依赖请求结束后的清理。

## 审核预览

低配实例保持：

```env
REVIEW_ALBUM_SIZE=5
REVIEW_PREVIEW_INTERVAL_SECONDS=0.75
REVIEW_PREVIEW_TIMEOUT_SECONDS=120
REVIEW_PREVIEW_THREAD=1
```

减小相册组能降低单次 Telegram 调用峰值，但增加消息数；不要把间隔设为 0 后再用更多
重试掩盖 FloodWait。

## PixivFlow 调度

同一 PixivFlow 实例内多个计划不要同时点火。把 Bot 2 的 Cron 错开 15–20 分钟，通常
比继续压 heap 更有效。保持 `download.concurrency=1`，并通过 delivery outbox 重试，
不要用并发重复投递换速度。

## 观测

```bash
curl -fsS http://127.0.0.1:8080/health
docker stats --no-stream telepost
flyctl machine status <machine-id> --app <app>
```

重点看：

- `process_rss` 与 `system_available_mb`
- `volume.used_percent`
- `api_uploads.files`
- `delivery_outbox.files`、`failed_files`、`oldest_age_seconds`
- `review_queue.pending` 与最老年龄

持续增长比单次峰值更值得告警。Fly `/health` 目录统计缓存约 15 秒，适合探针，不适合
毫秒级监控。

## 搜索

`simple` 省内存，但中文按较粗粒度匹配；`jieba` 搜索质量更好。完全不需要搜索时设置
`SEARCH_ENABLED=false`，启动、发布和磁盘都不会维护索引。索引不同步优先执行 `sync`，
仅在 Schema 变化或损坏时 `rebuild`。
