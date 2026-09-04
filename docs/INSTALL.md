# 安装与部署

## 选择方式

| 方式 | 适合 | 需要 |
|---|---|---|
| Release 单文件 | 最少依赖、单 Bot | 无需预装 Python |
| 源码 + venv | 开发、自管 VPS | Python 3.9+ |
| Docker / Compose | 通用生产环境 | Docker |
| Fly.io | Webhook、自动休眠 | `flyctl` |

PythonAnywhere 的旧适配不是当前受支持的生产路径，见
[PYTHONANYWHERE_DEPLOYMENT.md](PYTHONANYWHERE_DEPLOYMENT.md)。

## Release 单文件

从 [Releases](https://github.com/redtidev1918/TelePost/releases/latest) 下载：

- `telepost-linux-x64`
- `telepost-windows-x64.exe`
- `telepost-macos-arm64`

Linux/macOS：

```bash
chmod +x telepost-*
./telepost-linux-x64
```

首次运行会询问 Token、频道和 Owner ID，并在程序同目录写入 `config.ini`。再次运行启动；
以后可执行 `./telepost-linux-x64 --setup` 重配。macOS 产物只支持 Apple Silicon，Intel
Mac 请使用源码或 Docker。

## 源码运行

```bash
git clone https://github.com/redtidev1918/TelePost.git
cd TelePost
python3 -m venv .venv
./.venv/bin/pip install -r requirements.txt
./.venv/bin/python run.py --setup
./.venv/bin/python run.py
```

`run.py` 是统一入口；不要直接用 `main.py` 启动多 Bot。配置可改为环境变量，见
[CONFIGURATION.md](CONFIGURATION.md)。

仓库也保留了 `quickstart.sh`、`install.sh`、`start.sh`、`restart.sh` 和 `update.sh`，
适合交互式安装；自动化环境建议使用上面的显式命令。

## Docker Compose

在仓库根目录创建 `.env`：

```env
TOKEN=123456:replace-me
CHANNEL_ID=@your_channel
OWNER_ID=123456789
```

启动：

```bash
docker compose pull
docker compose up -d
docker compose logs -f telepost
```

`docker-compose.yml` 默认使用 `ghcr.io/redtidev1918/telepost:latest`，并把 `./data`、
`./logs` 挂载到容器。生产环境建议固定版本，例如 `2.10.39`，升级前备份 `data/`。

需要 Webhook 时自行映射 8080 并提供公网 HTTPS 反向代理；无公网地址保持
`RUN_MODE=AUTO` 或 `POLLING`。

## Fly.io

Fly.io 使用预构建镜像、持久卷和 Webhook。单 Bot、双 Bot以及“PixivFlow 常驻 +
TelePost 自动休眠”完整配置见 [FLYIO_DEPLOYMENT.md](FLYIO_DEPLOYMENT.md)。

## 首次运行核对

1. Bot 已加入频道并有发帖权限。
2. `OWNER_ID` 是个人用户 ID，不是群或频道 ID。
3. 审核群与投稿频道不是同一个会话。
4. `curl http://127.0.0.1:8080/health` 返回 200。
5. 向 Bot 发送 `/start` 和一次测试投稿。
6. Webhook 部署再检查 `getWebhookInfo` 的 URL、待处理数和最近错误。

## 升级与卸载

- 单文件：停止旧进程，备份同目录的 `config.ini` 与 `data/`，替换可执行文件。
- Git：备份数据后 `git pull --ff-only`，更新依赖并重启。
- Compose：固定新镜像版本，`docker compose pull && docker compose up -d`。
- Fly.io：先建 Volume snapshot，再更新固定版本镜像。

卸载程序前先保存 `data/`；SQLite、运行时策略和会话持久化都在其中。
