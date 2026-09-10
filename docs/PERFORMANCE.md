# 性能与容量

## 推荐档位

| 场景 | 内存 | 关键配置 |
|---|---:|---|
| 单 Bot、simple 搜索 | 256 MiB 起 | `SEARCH_ANALYZER=simple`、`DB_CACHE_KB=1024` |
| 单 Bot、jieba 搜索 | 512 MiB | 默认 `jieba`、`DB_CACHE_KB=4096` |
| 双 Bot TelePost | 512 MiB | 低配时 `SEARCH_ENABLED=false`、`DB_CACHE_KB=1024` |
| 独立 PixivFlow scheduler | 256 MiB | `download.concurrency=1`、Node heap 96 MiB |

256 MiB 只描述 TelePost standalone 的起步档位，不是资源保证。双 Bot + PixivFlow combined
runtime 的 Node、多个 Python 进程、图片上传和 ffmpeg 峰值相加，不能套用 standalone 数字。
Fly.io 推荐拆成 PixivFlow 常驻、TelePost 自动休眠，并以生产 RSS 实测选择内存。

## 主要内存来源

- 每个 Bot 是独立 Python 进程；多 Bot 另有父路由进程。
- `jieba` 词典与 Whoosh 索引明显增加常驻内存。
- SQLite cache 近似受 `DB_CACHE_KB` 控制。
- API 请求是流式传输，但并发上传、Telegram 重发和预览仍会形成峰值。
- PixivFlow 的 Node/V8 基线与下载解码峰值不能靠 Python 配置消除。

压缩文件大小不等于解码内存。图片解码工作集至少约为 `width × height × bytes_per_pixel`；
RGBA 按至少 4 B/px 估算，转换 RGB 还会产生额外工作集。TelePost 先读取文件大小、格式、
尺寸、模式和帧数等 header metadata，再比较 `TELEPOST_IMAGE_DECODE_BUDGET_MB`（默认 64 MiB）。
超过预算是硬边界：不会调用 `load`、`convert`、`resize` 或 `thumbnail`。

符合 Telegram photo 限制的原文件直接 pass-through。需要转换且预算允许时，TelePost 只生成
有界质量次数的 tempfile JPEG，原文件不变；预算不足、图片损坏、Pillow 缺失或压缩失败时，
审核优先显示可选 preview，最终素材使用 immutable original document。document fallback 是
低内存安全策略，不是异常，也不承诺保留相册式图片展示。

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

审核节流推荐保持：

```env
REVIEW_ALBUM_SIZE=5
REVIEW_PREVIEW_INTERVAL_SECONDS=0.75
REVIEW_PREVIEW_TIMEOUT_SECONDS=120
REVIEW_PREVIEW_THREAD=1
```

`REVIEW_ALBUM_SIZE` 用于 Telegram FloodWait/分组行为，不是图片解码 OOM 的修复开关。
不要把间隔设为 0 后再用更多重试掩盖 FloodWait。

## PixivFlow 调度

同一 PixivFlow 实例内多个计划不要同时点火。把 Bot 2 的 Cron 错开 15–20 分钟，通常
比继续压 heap 更有效。保持 `download.concurrency=1`，并通过 delivery outbox 重试，
不要用并发重复投递换速度。

## 观测

```bash
curl -fsS http://127.0.0.1:8080/live
curl -fsS http://127.0.0.1:8080/ready
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
