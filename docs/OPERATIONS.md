# 运维手册

> 最后更新：2026-08

## 发布流程（Tag → GHCR 镜像 → GitHub Release）

1. 把 `CHANGELOG.md` 的 `[Unreleased]` 内容整理进新版本段 `## [x.y.z] - 日期`
2. 必要时同步 `utils/helper_functions.py` 的 `CONFIG["VERSION"]`
3. 发布：
   ```bash
   git tag vx.y.z
   git push origin main --tags
   ```
4. GitHub Actions 自动完成：构建 amd64/arm64 镜像 → 推送
   `ghcr.io/redtidev1918/telepost:{x.y.z, x.y, latest}` → 创建 GitHub Release
   （正文取 CHANGELOG 对应版本段，缺失时回退 `[Unreleased]`）
5. 首次发布后：GitHub → Packages → `telepost` → Package settings 改为 Public（否则匿名 `docker pull` 需 `docker login ghcr.io`）

`docker-compose.yml` 已内置 `image: ghcr.io/redtidev1918/telepost:latest`，
不想本地构建的用户删掉 `build:` 段即可直接拉镜像运行。

## 启动 / 停止 / 重启

```bash
./start.sh          # 前台或后台启动（以脚本内实现为准）
./restart.sh        # 重启
./update.sh         # 拉取更新并重启
```
Docker 部署使用 `docker compose {up -d|restart|down}`；systemd 部署用 `systemctl` 管理。

## 日志

- 位置：`logs/`；每天 03:00 自动清理过期日志（`main.py` 定时任务）。
- `/health`：Polling 模式由 `health.py` 提供（8080 端口），Webhook 模式由 webhook 服务器提供。

## 数据库维护

- **备份**：先 `sqlite3 data/submissions.db "PRAGMA wal_checkpoint(FULL);"`，再拷贝 `submissions.db`（连同 `-wal`/`-shm` 更稳妥）。
- **优化**：`python3 optimize_database.py`（VACUUM/ANALYZE）。
- **重复数据清理**：`python3 cleanup_duplicates.py`（先 `--help` 核对参数）。
- **统计诊断**：`python3 diagnose_stats.py`。

## 搜索索引维护

```bash
python3 -m utils.index_manager status     # 查看索引与库的差异
python3 -m utils.index_manager sync       # 增量同步
python3 -m utils.index_manager rebuild    # 全量重建（可加 --no-clear）
python3 -m utils.index_manager optimize   # 合并索引段
```
对应机器人内管理员命令：`/index_stats` `/sync_index` `/rebuild_index` `/optimize_index`。
何时需要重建：Schema 变更、`status` 显示持续不同步、搜索结果明显缺失。

## 内置定时任务（main.py）

| 任务 | 周期 | 说明 |
|---|---|---|
| 过期投稿清理 | 每 5 分钟 | 删除 `TIMEOUT`（默认 300s）前的 submissions |
| 帖子统计更新 | 每 2 小时 | 拉取浏览/转发并计算热度 |
| 已删除消息检查 | 每 30 分钟 | 转发探测（需配置 `OWNER_ID`），自动标记 |
| 日志清理 | 每天 03:00 | |

## 迁移与配置工具

`migrate_to_search.py` / `migrate_add_filename.py` / `migrate_extract_filenames.py`（一次性迁移）、`check_config.py`（配置自检）、`setup_wizard.py`（交互式配置）、`scripts/crawl_channel_history.py`（频道历史抓取）。

---
最后更新：2026-08
