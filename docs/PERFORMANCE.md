# 性能与内存调优

> 最后更新：2026-09。旧《MEMORY_USAGE》中与代码不符的数字已按当前实现更正。
> 2026-09 在 Fly 联合档（TelePost 2.10.27 ×2 + PixivFlow 2.10.26，512MB 机器）
> 实测空闲基线：supervisor `run.py` ≈ 39MB、每个 bot `main.py` ≈ 55–58MB、
> PixivFlow node ≈ 78MB，合计 ≈ **230MB**（另有 Fly hallpass 侧车 ≈ 16MB）。

## 内存构成（实测/估算）

| 组成 | 占用（约） | 说明 |
|---|---|---|
| Python + 依赖基线 | 实测 ≈55MB/进程 | python-telegram-bot 21 + aiohttp + aiosqlite；想再低只能砍功能 |
| Whoosh（simple 分词） | +10–20 MB | 默认可运行档；联合档已 `SEARCH_ENABLED=false` 不加载 |
| Whoosh（jieba 分词） | +100 MB 量级 | 需要 512MB 档；jieba 未安装时自动回退 simple |
| SQLite cache | `DB_CACHE_KB`，默认 ≈4 MB | 联合档已降至 1024（≈1MB） |
| PixivFlow（可选） | node 单进程实测 ≈78MB | V8 运行时基线已占大头；联合档 heap 限 96MB（文档旧值 128 偏松） |
| supervisor `run.py` | 实测 ≈39MB | 多 bot 父路由，须常驻 |

**结论：上述 230MB 已是调优后的地板**——把 `SEARCH_ENABLED=false`、`DB_CACHE_KB=1024`、
`NODE_OPTIONS=--max-old-space-size=96` 全部用上后，空闲占用 ≈ 232MB（+16MB 侧车）。
继续压榨只能靠"减少进程数/砍功能"，或**错开峰值**（见下），换更小机器档位不可行：
单次运行峰值（下载 + 双 bot 同时投稿）会超过 256MB 档。

## 峰值内存：错开两个 Cron 是唯一无损耗手段

联合档最危险的时刻是**两个 Cron 同时点火**（下载高峰期双 bot 同时投稿）。
实测两个计划都是 `0 10,18 * * *`（同时跑）时，峰值接近上限；把第二个计划错开
15～20 分钟（如 bot2 用 `10 10,18 * * *`），峰值显著回落且零功能损失。
这是"减少内存占用"里收益最大的一步——**改完无需动任何代码/配置项**，
只改 volume 上 config.json 的 cron 并等热加载生效。

## 可调项

| 配置 | 默认 | 调整建议 |
|---|---|---|
| `SEARCH_ANALYZER` | `jieba` | 256MB 环境改 `simple`（中文退化为整词匹配） |
| `DB_CACHE_KB` | `4096` | 小内存可降至 `1024` |
| `SEARCH_HIGHLIGHT` | `false` | 保持默认即可（开启增加开销） |
| `SEARCH_ENABLED` | `true` | 完全禁用搜索：启动不建索引、发布不写索引 |
| `ALLOWED_TAGS` / `TIMEOUT` | 30 / 300 | 影响很小，按需 |

脚本 `switch_mode.sh` / `optimize_memory.sh` 可一键切换预设（以脚本内容为准）。

## 发布行为与资源

- 多媒体发布按每组 ≤10 个分块、组间 2 秒延迟（规避 API 限流），多组消息以"回复"串联。
- 多 Bot HTTP 父路由与 Telegram 上传都使用 64 KiB 分块流式传输；即使 API 接收
  大型媒体组，也不会在父进程和子进程各复制一份完整请求体。连接池在父路由生命周期
  内复用，减少每次投稿的握手和临时对象。
- `data/api_uploads` 的每请求目录会在成功、校验失败或发布异常后统一删除；若该指标
  持续增长，通常表示进程被强制终止，应检查 OOM/平台关机日志。
- 索引写入仅在 `SEARCH_ENABLED=true` 时进行；禁用搜索的部署不会在磁盘产生索引目录。
- Docker 使用多阶段构建：编译器、Python 头文件、npm 与测试工具只存在于构建阶段；
  默认镜像不含 Node，Fly 联合档显式选择 `runtime-pixivflow`，仅额外复制 Node 24 LTS
  运行时和固定版本的 PixivFlow 安装目录。
- 生产依赖安装自 `requirements.txt`；开发和测试环境使用 `requirements-dev.txt`。

## 磁盘与 outbox 观测

联合档的 `/health` 会报告持久卷容量与使用率、PixivFlow cache、delivery outbox 和
API 临时上传目录。outbox 还包含待交付文件数、总重试次数、带错误的文件数与最老
任务年龄；这些数字持续增加通常说明 Telegram/API 交付链路异常。目录扫描结果缓存
15 秒，因此健康探针不会每次都遍历整个缓存。

缓存模式下不要直接清空 outbox 或其引用的下载文件。先恢复交付链路并让 PixivFlow
在下次任务执行时重试；只有确认稿件无需投递后，才手工清理对应清单和缓存。

## 两档推荐配置

**256MB（简单分词档）**：`SEARCH_ANALYZER=simple`、`DB_CACHE_KB=1024`。
**512MB（高质量分词档）**：安装 `jieba` 并 `SEARCH_ANALYZER=jieba`、`DB_CACHE_KB=4096`。

**512MB（双 Bot + PixivFlow 联合档）**：这与上面的“高质量分词档”互斥。
设置 `SEARCH_ENABLED=false`、`DB_CACHE_KB=1024`、`PIXIV_DB_CACHE_KB=4096`、
`NODE_OPTIONS=--max-old-space-size=96`（实测 96 足够，128 偏松），PixivFlow
`download.concurrency=1`，两个 Cron **至少错开 15～20 分钟**（同刻点火是唯一
明显的峰值超限来源）。WebUI、jieba、并发 PixivFlow 任务均不要开启。

## 索引健康

启动时自动检查并同步/重建；日常用 `python3 -m utils.index_manager status` 巡检（见 [OPERATIONS](OPERATIONS.md)）。全量重建代价与帖子数线性相关，尽量用 `sync`。

---
最后更新：2026-09
