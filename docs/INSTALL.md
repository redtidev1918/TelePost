# 安装与部署

> **语言 / Language：** 中文 · [English](en/INSTALL.md)

这是 TelePost 安装、升级与卸载的唯一权威入口。README 只给出最短路径，具体命令以本页为准。

## 选择安装方式

| 方式 | 适合 | 需要 |
|---|---|---|
| **pip（推荐）** | 最简单的 Python 安装与自动化：一条命令安装、一条命令升级 | Python 3.10+，系统 libvips |
| Release 单文件 | 不希望安装 Python 的用户 | 无需预装 Python |
| Docker / Compose | 容器部署 | Docker |
| Fly.io | 常驻生产 Webhook | `flyctl` |
| 源码 + venv | 开发、自管 VPS | Python 3.10+ |

**版本号必须来自正式 Release**（见 [运维手册 · 正式发布流程](OPERATIONS.md#正式发布流程)）。
示例中的 `<version>` 形如 `2.x.y`，请替换成你要部署的那个 release version；生产环境不要使用
会随时间漂移的 `latest`。

PythonAnywhere 的旧 WSGI 适配**不是当前受支持的生产路径**：它覆盖不了完整运行生命周期
（多 Bot supervisor、webhook 注册与审核队列）。旧文档曾把它描述为「已验证可用」，那是不准确的，
不要按旧教程部署到生产。

## 1. pip 安装（推荐）

```bash
pip install telepost-bot
telepost --setup
telepost
```

| 名称 | 值 |
|---|---|
| PyPI 项目名 | `telepost-bot` |
| 命令行 | `telepost` |
| Python 导入 | `telepost` |
| Python | 3.10+ |

`telepost --setup` 是可选的配置向导：跳过它直接运行 `telepost` 时，首次启动同样会进入同一个
向导，填写 **Bot Token**、**频道 ID**、**Owner ID**。再次运行则以前次配置直接启动。

升级：

```bash
pip install --upgrade telepost-bot
```

### libvips 系统依赖

`pyvips` 是 ctypes 封装，不打包 libvips 本体。需要在操作系统层面安装：

| 系统 | 命令 |
|---|---|
| macOS (Homebrew) | `brew install vips` |
| Ubuntu / Debian | `sudo apt-get install libvips42` |
| Fedora | `sudo dnf install vips` |
| Arch Linux | `sudo pacman -S libvips` |
| Windows | 从 [libvips releases](https://github.com/libvips/libvips/releases) 下载，将 `bin` 目录加入 `PATH` |

未安装 libvips 时，`telepost` 仍可启动并完成基本投稿流程，但超大图片的流式缩放会不可用。

## 2. Release 单文件

不想安装 Python 时使用。从 [Releases](https://github.com/redtidev1918/TelePost/releases/latest)
下载对应平台的产物：

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
Mac 请使用 pip、源码或 Docker。

## 3. Docker / Compose

镜像统一为 `ghcr.io/redtidev1918/telepost:<version>`。在仓库根目录创建 `.env`：

```env
TOKEN=123456:replace-me
CHANNEL_ID=@your_channel
OWNER_ID=123456789
TELEPOST_VERSION=2.x.y    # 替换成要固定的 release version，例如 PYPI/GHCR 上的正式版本号
```

启动：

```bash
docker compose pull
docker compose up -d
docker compose logs -f telepost
```

`docker-compose.yml` 通过 `TELEPOST_VERSION` 决定镜像版本；不设置时会落到 `latest`，
那只适合本机试用。**生产必须固定 release version**，不要依赖 `latest`。也可以直接用 `docker run`：

```bash
docker run -d --name telepost --restart unless-stopped \
  --env-file .env -p 8080:8080 \
  -v "$PWD/data:/app/data" -v "$PWD/logs:/app/logs" \
  ghcr.io/redtidev1918/telepost:<version>
```

容器会把 `./data`、`./logs` 挂载出来；升级前备份 `data/`。

需要 Webhook 时自行映射 8080 并提供公网 HTTPS 反向代理；无公网地址保持
`RUN_MODE=AUTO` 或 `POLLING`。

## 4. 源码运行

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

## 5. Fly.io

Fly.io 使用预构建镜像、持久卷和 Webhook。TelePost 保持常驻，避免投稿入口出现冷启动延迟；
单 Bot、多 Bot 和可选 PixivFlow 组合见 [FLYIO_DEPLOYMENT.md](FLYIO_DEPLOYMENT.md)。

生产发版不需要手工 `flyctl deploy`：合并 Release PR 后，流水线会把同一版本的 GHCR 镜像部署到
Fly 并校验健康与版本，详见 [FLYIO_DEPLOYMENT.md § 0](FLYIO_DEPLOYMENT.md#0-正式发版路径唯一推荐)。

## 首次运行核对

1. Bot 已加入频道并有发帖权限。
2. `OWNER_ID` 是个人用户 ID，不是群或频道 ID。
3. 审核群与投稿频道不是同一个会话。
4. `curl http://127.0.0.1:8080/health` 返回 200。
5. 向 Bot 发送 `/start` 和一次测试投稿。
6. Webhook 部署再检查 `getWebhookInfo` 的 URL、待处理数和最近错误。

## 统一升级模型

| 部署方式 | 升级命令 | 说明 |
|---|---|---|
| pip | `pip install --upgrade telepost-bot` | 升级后重启进程 |
| Release 单文件 | 停止旧进程 → 替换可执行文件 → 启动 | 备份同目录 `config.ini` 与 `data/` |
| Docker / Compose | 改 `TELEPOST_VERSION=新版本` → `docker compose pull && docker compose up -d` | 或直接改 `image` 的固定 TAG |
| Fly.io | `flyctl machine update <machine-id> --app <app> --image ghcr.io/redtidev1918/telepost:<version> --yes` | 仅自建实例/故障恢复；正式发版走流水线 |
| 源码 | `git pull --ff-only` → 更新依赖 → 重启 | 先备份 `data/` |

三条规则：

1. **生产不推荐 `latest`**。`latest` 会随时间变化，无法与某个 Release 对应，出问题也无法回滚到确定的构建。
2. **版本号来自正式 Release**，不要自己编造版本，也不要从分支或 commit 部署。
3. 升级前先备份 `data/`（SQLite 使用 WAL，备份与回滚见 [运维手册](OPERATIONS.md)）。

## 卸载

卸载程序前先保存 `data/`；SQLite、运行时策略和会话持久化都在其中。
