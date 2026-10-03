# Install and deploy

**Language / 语言:** [中文](/INSTALL.md) · English

This is the authoritative entry point for installing, upgrading and uninstalling TelePost. The
README only shows the shortest path; the commands live here.

## Choosing an install method

| Method | Fits | Requires |
|---|---|---|
| **pip (recommended)** | Simplest Python install and automation: one command to install, one to upgrade | Python 3.10+, OS libvips |
| Release single file | Users who prefer not to install Python | No preinstalled Python |
| Docker / Compose | Container deployments | Docker |
| Fly.io | Resident production Webhook | `flyctl` |
| Source + venv | Development, self-managed VPS | Python 3.10+ |

**The version number always comes from an official release** (see
[Operations · release flow](/OPERATIONS.md#正式发布流程)（中文）). `<version>` in the examples means
a `2.x.y` release version — substitute the release you are deploying. Never use `latest` in
production: it drifts over time.

PythonAnywhere's legacy WSGI adapter is **not a supported production path**: it cannot cover
the full runtime lifecycle (multi-bot supervisor, webhook registration, review queue). Older
documentation described it as "verified working", which was inaccurate — do not deploy to
production following those old guides.

## 1. pip install (recommended)

```bash
pip install telepost-bot
telepost --setup
telepost
```

| Name | Value |
|---|---|
| PyPI project | `telepost-bot` |
| CLI command | `telepost` |
| Python import | `telepost` |
| Python | 3.10+ |

`telepost --setup` is the optional configuration wizard. Skipping it changes nothing: the first
`telepost` run enters the same wizard for the **Bot Token**, **Channel ID** and **Owner ID**, and
later runs start with the saved configuration.

Upgrade:

```bash
pip install --upgrade telepost-bot
```

### libvips system dependency

`pyvips` is a ctypes wrapper and does not bundle libvips itself. Install it at the OS level:

| OS | Command |
|---|---|
| macOS (Homebrew) | `brew install vips` |
| Ubuntu / Debian | `sudo apt-get install libvips42` |
| Fedora | `sudo dnf install vips` |
| Arch Linux | `sudo pacman -S libvips` |
| Windows | Download from [libvips releases](https://github.com/libvips/libvips/releases), add `bin` to `PATH` |

Without libvips installed, `telepost` can still start and handle basic submissions, but streaming
downscaling of very large images will be unavailable.

## 2. Release single file

For users who prefer not to install Python. Download the build for your platform from
[Releases](https://github.com/redtidev1918/TelePost/releases/latest):

- `telepost-linux-x64`
- `telepost-windows-x64.exe`
- `telepost-macos-arm64`

Linux/macOS:

```bash
chmod +x telepost-*
./telepost-linux-x64
```

The first run asks for the token, channel and owner ID, and writes `config.ini` next to the
executable. Running it again starts the bot; re-configure later with
`./telepost-linux-x64 --setup`. The macOS build is Apple Silicon only — Intel Mac users should
use pip, source or Docker.

## 3. Docker / Compose

The image is always `ghcr.io/redtidev1918/telepost:<version>`. Create `.env` in the repository root:

```env
TOKEN=123456:replace-me
CHANNEL_ID=@your_channel
OWNER_ID=123456789
TELEPOST_VERSION=2.x.y    # replace with the release version you want to pin
```

Start it:

```bash
docker compose pull
docker compose up -d
docker compose logs -f telepost
```

`docker-compose.yml` reads the image version from `TELEPOST_VERSION`; when it is unset the image
falls back to `latest`, which suits local trials only. **Pin a release version in production** —
do not rely on `latest`. Plain `docker run` works too:

```bash
docker run -d --name telepost --restart unless-stopped \
  --env-file .env -p 8080:8080 \
  -v "$PWD/data:/app/data" -v "$PWD/logs:/app/logs" \
  ghcr.io/redtidev1918/telepost:<version>
```

The container mounts `./data` and `./logs`; back up `data/` before upgrading.

If you need webhooks, map 8080 yourself and put a public HTTPS reverse proxy in front. Without
a public address, keep `RUN_MODE=AUTO` or `POLLING`.

## 4. Run from source

```bash
git clone https://github.com/redtidev1918/TelePost.git
cd TelePost
python3 -m venv .venv
./.venv/bin/pip install -r requirements.txt
./.venv/bin/python run.py --setup
./.venv/bin/python run.py
```

`run.py` is the single entry point; do not start the multi-bot supervisor directly with
`main.py`. Configuration can be supplied through environment variables instead — see
[Configuration](/en/CONFIGURATION.md).

The repository also keeps `quickstart.sh`, `install.sh`, `start.sh`, `restart.sh` and
`update.sh`, which suit interactive installation; prefer the explicit commands above in
automated environments.

## 5. Fly.io

Fly.io uses a prebuilt image, a persistent volume, and Webhooks. TelePost stays resident to avoid
cold-start delays on the submission path. Single-bot, multi-bot, and optional PixivFlow integration
are documented in [Fly.io deployment](/FLYIO_DEPLOYMENT.md)（中文）.

Production releases do **not** need a manual `flyctl deploy`: once the Release PR is merged, the
pipeline deploys the same GHCR image to Fly and verifies health and version — see
[Fly.io deployment § 0](/FLYIO_DEPLOYMENT.md#0-正式发版路径唯一推荐)（中文）.

## First-run checklist

1. The bot has joined the channel and may post there.
2. `OWNER_ID` is a personal user ID, not a group or channel ID.
3. The review group and the submission channel are different chats.
4. `curl http://127.0.0.1:8080/health` returns 200.
5. Send `/start` and one test submission to the bot.
6. For a webhook deployment, also check the URL, pending count and last error from `getWebhookInfo`.

## Unified upgrade model

| Deployment | Upgrade command | Notes |
|---|---|---|
| pip | `pip install --upgrade telepost-bot` | Restart the process afterwards |
| Release single file | Stop → replace the executable → start | Back up `config.ini` and `data/` first |
| Docker / Compose | Set `TELEPOST_VERSION=<new version>` → `docker compose pull && docker compose up -d` | Or pin the tag directly in `image` |
| Fly.io | `flyctl machine update <machine-id> --app <app> --image ghcr.io/redtidev1918/telepost:<version> --yes` | Self-managed instances and recovery only; releases go through the pipeline |
| Source | `git pull --ff-only` → update dependencies → restart | Back up `data/` first |

Three rules:

1. **No `latest` in production.** It changes over time, cannot be mapped to a release, and leaves
   nothing deterministic to roll back to.
2. **Versions come from official releases.** Do not invent version numbers or deploy from a branch
   or commit.
3. Back up `data/` before upgrading (SQLite runs in WAL mode; backups and rollbacks are covered in
   [Operations](/OPERATIONS.md)（中文）).

## Uninstall

Save `data/` before uninstalling — SQLite, runtime policy and session persistence all live there.

> Pages marked **（中文）** are currently Chinese-only. Their English versions are being added
> incrementally.
