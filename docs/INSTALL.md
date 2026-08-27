# 安装与部署

> 最后更新：2026-08。所有方式共用同一份配置（见 [CONFIGURATION.md](CONFIGURATION.md)），最少需要 `TOKEN` 与 `CHANNEL_ID` 两项，缺失会直接启动失败。

## 选择部署方式

| 方式 | 适合场景 | 前置要求 |
|---|---|---|
| [quickstart.sh](#1-quickstart-快速体验) | 本地快速体验 | Python 3.9+ |
| [install.sh + systemd](#2-vps-生产部署) | 生产 VPS | root/sudo 的 Linux |
| [Docker / Compose](#3-docker) | 容器环境 | Docker |
| [Fly.io](FLYIO_DEPLOYMENT.md) | 免运维 PaaS（Webhook） | flyctl |
| [PythonAnywhere](PYTHONANYWHERE_DEPLOYMENT.md) | 低成本托管（Webhook） | 账号 |

## 1. Quickstart（快速体验）

```bash
git clone https://github.com/redtidev1918/TeleSubmit-v2.git && cd TeleSubmit-v2
./quickstart.sh     # 引导式配置并启动
```

## 2. VPS 生产部署

```bash
sudo ./install.sh   # 创建 venv、写入 config.ini、注册 systemd 服务
```
管理：`systemctl {start|stop|restart|status} telesubmit`（服务名以脚本实际注册为准）。更新用 `./update.sh`。

## 3. Docker

```bash
docker compose up -d --build
```

- 镜像内置 `HEALTHCHECK`，每 30 秒请求 `http://localhost:8080/health`：
  - **Polling 模式**：由 `health.py`（独立守护线程，默认 8080 端口）提供；
  - **Webhook 模式**：由 `utils/webhook_server.py` 在同一端口提供 `/webhook` 与 `/health`。
- 持久化：容器内 `data/`（数据库+索引）与 `logs/` 需要卷映射，参考 `docker-compose.yml`。

## 4/5. Fly.io 与 PythonAnywhere

见对应指南：[FLYIO_DEPLOYMENT.md](FLYIO_DEPLOYMENT.md)、[PYTHONANYWHERE_DEPLOYMENT.md](PYTHONANYWHERE_DEPLOYMENT.md)。两者均为 Webhook 模式；**设置密钥时使用 `TOKEN`**（`BOT_TOKEN` 亦兼容）：

```bash
flyctl secrets set TOKEN=xxx CHANNEL_ID=@xxx OWNER_ID=xxx WEBHOOK_URL=https://xxx.fly.dev
```

## 首次启动核对清单

- [ ] 日志出现 `配置加载完成`、`数据库初始化完成`、命令菜单设置成功
- [ ] `curl localhost:8080/health` 返回 200
- [ ] 向机器人发送 `/start` 有响应；`/submit` 能进入投稿流程
- [ ] 完成一次投稿，频道收到消息且 OWNER 收到通知

## 更新与卸载

- 更新：`./update.sh`（拉取代码 + 依赖 + 重启）
- 卸载：`./uninstall.sh`（含 systemd 服务清理）

---
最后更新：2026-08
