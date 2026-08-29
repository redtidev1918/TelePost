# 性能与内存调优

> 最后更新：2026-08。旧《MEMORY_USAGE》中与代码不符的数字已按当前实现更正。

## 内存构成（估算）

| 组成 | 占用（约） | 说明 |
|---|---|---|
| Python + 依赖基线 | 60–80 MB | python-telegram-bot、aiohttp、aiosqlite |
| Whoosh（simple 分词） | +10–20 MB | 默认可运行档 |
| Whoosh（jieba 分词） | +100 MB 量级 | 需要 512MB 档；jieba 未安装时自动回退 simple |
| SQLite cache | `DB_CACHE_KB`，默认 ≈4 MB | 旧文档"默认 20MB"有误 |
| PixivFlow（可选） | Node 单进程，随任务量变化 | 512 MiB 联合档限制 V8 heap 128MB、SQLite 4MB、下载并发 1 |

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
- 索引写入仅在 `SEARCH_ENABLED=true` 时进行；禁用搜索的部署不会在磁盘产生索引目录。
- Docker 使用多阶段构建：编译器、Python 头文件、npm 与测试工具只存在于构建阶段；
  默认镜像不含 Node，Fly 联合档显式选择 `runtime-pixivflow`，仅额外复制 Node 运行时
  和 PixivFlow 安装目录。
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
`NODE_OPTIONS=--max-old-space-size=128`，PixivFlow `download.concurrency=1`，两个
Cron 至少错开 15～20 分钟。WebUI、jieba、并发 PixivFlow 任务均不要开启。

## 索引健康

启动时自动检查并同步/重建；日常用 `python3 -m utils.index_manager status` 巡检（见 [OPERATIONS](OPERATIONS.md)）。全量重建代价与帖子数线性相关，尽量用 `sync`。

---
最后更新：2026-08
