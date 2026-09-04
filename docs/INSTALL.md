# 安装与部署

> 最后更新：2026-09。所有方式共用同一份配置（见 [CONFIGURATION.md](CONFIGURATION.md)），最少需要 `TOKEN` 与 `CHANNEL_ID` 两项，缺失会直接启动失败。

## 选择部署方式

| 方式 | 适合场景 | 前置要求 |
|---|---|---|
| [下载即用](#0-下载即用零依赖) | Windows/macOS/Linux 桌面 | 无（自带运行环境） |
| [quickstart.sh](#1-quickstart-快速体验) | 本地快速体验 | Python 3.9+（Linux/macOS） |
| [install.sh + systemd](#2-vps-生产部署) | 生产 VPS | root/sudo 的 Linux |
| [install.bat](#4-windows-原生) | Windows 本地（无需 WSL/Docker） | Windows + Python 3.9+ |
| [Docker / Compose](#3-docker) | 容器环境 | Docker |
| [Fly.io](FLYIO_DEPLOYMENT.md) | 免运维 PaaS（Webhook） | flyctl |
| [PythonAnywhere](PYTHONANYWHERE_DEPLOYMENT.md) | 低成本托管（Webhook） | 账号 |

## 0. 下载即用（零依赖）

不需要安装 Python / Docker / WSL 任何东西——可执行文件里已内置运行环境：

1. 从 [GitHub Releases](https://github.com/redtidev1918/TelePost/releases) 下载对应平台的
   `telepost-<平台>`（v2.10.34 起每次发版自动生成）；Apple Silicon Mac 下载 `macos-x64` 版（Rosetta 自动转译）；
2. 双击/运行：首次会自动进入配置向导，按提示填 **Bot Token、频道、你的 ID** 三项即可；
3. 再次运行即启动，配置与数据都保存在可执行文件同目录（`config.ini` / `data/` / `logs/`）。

小提示：Windows 首次运行若弹 SmartScreen，选"更多信息 → 仍要运行"；macOS 若被 Gatekeeper 拦截，
右键文件 → 打开。想要开机自启可在系统里把它设为登录项。

## 1. Quickstart（快速体验）

```bash
git clone https://github.com/redtidev1918/TelePost.git && cd TelePost
./quickstart.sh     # 引导式配置并启动
```

## 2. VPS 生产部署

```bash
sudo ./install.sh   # 创建 venv、写入 config.ini、注册 systemd 服务
```
管理：`systemctl {start|stop|restart|status} telepost`（服务名以脚本实际注册为准）。更新用 `./update.sh`。

## 3. Docker

拉预构建镜像（不需要本地源码）：

```bash
mkdir telepost && cd telepost
# 建 .env（TOKEN/CHANNEL_ID/OWNER_ID 三行，compose 自动读取），然后：
docker compose up -d
```

镜像内置 `HEALTHCHECK`，每 30 秒请求 `http://localhost:8080/health`：
  - **Polling 模式**：由 `utils/polling_server.py` 在同一事件循环提供 `/health` 与 `/api/v1/*`；
  - **Webhook 模式**：由 `utils/webhook_server.py` 在同一端口提供 `/webhook`、`/health` 与 `/api/v1/*`。
- 默认 `RUN_MODE=AUTO`：配置有效公网 HTTPS `WEBHOOK_URL` 时使用 Webhook，否则自动使用 Polling；Webhook 注册失败也会安全回退 Polling。
- 持久化：容器内 `data/`（数据库+索引）与 `logs/` 需要卷映射，参考 `docker-compose.yml`。
- 离线/受限网络拉不动镜像时，改用 `docker compose up -d --build` 本地构建。

## 4. Windows 原生

不需要 WSL 或 Docker（Windows 版 Python 官方安装包自带 venv/pip，无 Linux 的 `python3-venv` 问题）：

1. 到 https://www.python.org/downloads/ 装 Python 3.9+，安装时勾选 **Add python.exe to PATH**；
2. 双击 `install.bat`（建 venv、装依赖、引导生成 `config.ini`）；
3. 双击 `run.bat` 启动，窗口关着就停；要开机自启用「任务计划程序」加一条登录时运行 `run.bat`。

长期在线的服务端仍建议 Linux/Docker/Fly，桌面本机 Windows 用上面两个 bat 即可。

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
