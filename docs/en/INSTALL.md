# Install and deploy

**Language / 语言:** [中文](/INSTALL.md) · English

## Choosing a method

| Method | Fits | Requires |
|---|---|---|
| Release single file | Minimal dependencies, a single bot | No preinstalled Python |
| Source + venv | Development, self-managed VPS | Python 3.9+ |
| Docker / Compose | General production | Docker |
| Fly.io | Webhook, autosleep | `flyctl` |

PythonAnywhere's legacy WSGI adapter is **not a supported production path**: it cannot cover
the full runtime lifecycle (multi-bot supervisor, webhook registration, review queue). Older
documentation described it as "verified working", which was inaccurate — do not deploy to
production following those old guides.

## Release single file

Download from [Releases](https://github.com/redtidev1918/TelePost/releases/latest):

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
use source or Docker.

## Run from source

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
[CONFIGURATION.md](/CONFIGURATION.md)（中文）.

The repository also keeps `quickstart.sh`, `install.sh`, `start.sh`, `restart.sh` and
`update.sh`, which suit interactive installation; prefer the explicit commands above in
automated environments.

## Docker Compose

Create `.env` in the repository root:

```env
TOKEN=123456:replace-me
CHANNEL_ID=@your_channel
OWNER_ID=123456789
```

Start it:

```bash
docker compose pull
docker compose up -d
docker compose logs -f telepost
```

`docker-compose.yml` uses `ghcr.io/redtidev1918/telepost:latest` by default and mounts
`./data` and `./logs` into the container. Pin an explicit version in production, for example
`2.10.39`, and back up `data/` before upgrading.

If you need webhooks, map 8080 yourself and put a public HTTPS reverse proxy in front. Without
a public address, keep `RUN_MODE=AUTO` or `POLLING`.

## Fly.io

Fly.io uses a prebuilt image, a persistent volume and webhooks. Full configurations for a
single bot, two bots, and "PixivFlow always-on + TelePost autosleep" are in
[FLYIO_DEPLOYMENT.md](/FLYIO_DEPLOYMENT.md)（中文）.

## First-run checklist

1. The bot has joined the channel and may post there.
2. `OWNER_ID` is a personal user ID, not a group or channel ID.
3. The review group and the submission channel are different chats.
4. `curl http://127.0.0.1:8080/health` returns 200.
5. Send `/start` and one test submission to the bot.
6. For a webhook deployment, also check the URL, pending count and last error from `getWebhookInfo`.

## Upgrade and uninstall

- Single file: stop the old process, back up `config.ini` and `data/` from the same directory, then replace the executable.
- Git: back up data, then `git pull --ff-only`, update dependencies and restart.
- Compose: pin the new image version, then `docker compose pull && docker compose up -d`.
- Fly.io: take a volume snapshot first, then update to the pinned image version.

Save `data/` before uninstalling — SQLite, runtime policy and session persistence all live there.

> Pages marked **（中文）** are currently Chinese-only. Their English versions are being added
> incrementally.
