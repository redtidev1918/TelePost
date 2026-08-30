"""
TelePost 启动器

- 未设置 BOT1_TOKEN：单 bot 模式，直接运行 main.py（行为与旧版完全一致）
- 设置了 BOT1_TOKEN：多 bot 模式，为每个 bot（BOT1、BOT2、…）派生独立子进程，
  各自使用独立的 TOKEN/CHANNEL_ID/OWNER_ID 与独立数据目录（data/botN/），
  一台机器承载多个频道，节省开销。

任一子进程异常退出时自动重启（5 秒退避）；SIGTERM/SIGINT 会转发给所有子进程。
"""
import asyncio
import json
import os
import re
import shlex
import shutil
import signal
import sqlite3
import subprocess
import sys
import threading
import time

from utils.run_mode import resolve_run_mode

# 子进程可按 BOT{n}_<KEY> 覆盖的配置项
OVERRIDABLE_KEYS = (
    "OWNER_ID", "ADMIN_IDS", "SHOW_SUBMITTER", "NOTIFY_OWNER",
    "BOT_MODE", "ALLOWED_FILE_TYPES", "SUBMIT_LIMIT_PER_HOUR",
    "API_REVIEW_REQUIRED", "CHAT_REVIEW_REQUIRED", "REVIEW_CHAT_ID",
    "DB_PATH", "SEARCH_INDEX_DIR", "SEARCH_ENABLED", "SEARCH_ANALYZER",
    "HEALTH_PORT", "TIMEOUT", "RUN_MODE", "WEBHOOK_SECRET_TOKEN",
)

# 多 bot webhook 模式下的端口/路径分配：
#   路由进程占 WEBHOOK_PORT（默认 8080，对外 + Fly 健康检查）
#   botN 子进程占 8080+N（仅本机回环可见），webhook 路径为 /webhook/botN
ROUTER_DEFAULT_PORT = 8080
ROUTER_CLIENT_MAX_BYTES = 510 * 1024 * 1024
_STORAGE_METRICS_CACHE = {"expires_at": 0.0, "value": None}


def _directory_metrics(path: str, *, suffix: str | None = None) -> dict:
    """Return bounded, symlink-safe file counts and bytes for one directory."""
    total_bytes = 0
    file_count = 0
    if not os.path.isdir(path):
        return {"files": 0, "bytes": 0, "mb": 0.0}
    for root, _dirs, files in os.walk(path, followlinks=False):
        for filename in files:
            if suffix and not filename.endswith(suffix):
                continue
            try:
                total_bytes += os.stat(
                    os.path.join(root, filename), follow_symlinks=False
                ).st_size
                file_count += 1
            except (FileNotFoundError, OSError):
                continue
    return {
        "files": file_count,
        "bytes": total_bytes,
        "mb": round(total_bytes / 1048576, 3),
    }


def _outbox_metrics(path: str) -> dict:
    """Summarize durable delivery manifests without exposing their contents."""
    metrics = _directory_metrics(path, suffix=".json")
    total_attempts = 0
    failed_files = 0
    oldest_mtime = None
    if os.path.isdir(path):
        for root, _dirs, files in os.walk(path, followlinks=False):
            for filename in files:
                if not filename.endswith(".json"):
                    continue
                manifest_path = os.path.join(root, filename)
                try:
                    stat = os.stat(manifest_path, follow_symlinks=False)
                    oldest_mtime = (
                        stat.st_mtime
                        if oldest_mtime is None
                        else min(oldest_mtime, stat.st_mtime)
                    )
                    with open(manifest_path, encoding="utf-8") as handle:
                        manifest = json.load(handle)
                    total_attempts += max(0, int(manifest.get("attempts", 0)))
                    failed_files += int(bool(manifest.get("lastError")))
                except (FileNotFoundError, OSError, ValueError, TypeError):
                    continue
    metrics.update({
        "total_attempts": total_attempts,
        "failed_files": failed_files,
        "oldest_age_seconds": (
            round(max(0.0, time.time() - oldest_mtime), 1)
            if oldest_mtime is not None
            else 0.0
        ),
    })
    return metrics


def _review_queue_metrics(values: dict) -> dict:
    """Summarize multi-Bot review queues without exposing submission content."""
    bots = []
    total_pending = 0
    total_failed = 0
    oldest_created_at = None
    for index in bot_indices(values):
        database_path = os.path.abspath(build_bot_env(index, values)["DB_PATH"])
        try:
            conn = sqlite3.connect(
                f"file:{database_path}?mode=ro", uri=True, timeout=0.5
            )
            try:
                counts = dict(conn.execute(
                    "SELECT status, COUNT(*) FROM pending_reviews GROUP BY status"
                ).fetchall())
                oldest = conn.execute(
                    "SELECT MIN(created_at) FROM pending_reviews WHERE status='pending'"
                ).fetchone()[0]
            finally:
                conn.close()
        except (OSError, sqlite3.Error):
            continue
        pending = int(counts.get("pending", 0))
        failed = int(counts.get("failed", 0))
        total_pending += pending
        total_failed += failed
        if oldest is not None:
            oldest_created_at = (
                float(oldest)
                if oldest_created_at is None
                else min(oldest_created_at, float(oldest))
            )
        bots.append({
            "bot": index,
            "pending": pending,
            "failed": failed,
            "expired": int(counts.get("expired", 0)),
        })
    return {
        "pending": total_pending,
        "failed": total_failed,
        "oldest_pending_age_seconds": (
            round(max(0.0, time.time() - oldest_created_at), 1)
            if oldest_created_at is not None
            else 0.0
        ),
        "by_bot": bots,
    }


def storage_health_snapshot(env=None, *, force: bool = False) -> dict:
    """Summarize the persistent volume and PixivFlow's transient storage."""
    global _STORAGE_METRICS_CACHE
    use_cache = env is None and not force
    now = time.monotonic()
    if use_cache and _STORAGE_METRICS_CACHE["expires_at"] > now:
        return _STORAGE_METRICS_CACHE["value"]

    values = env or os.environ
    config_path = os.path.abspath(values.get(
        "PIXIVFLOW_CONFIG", "/app/data/pixivflow/config.json"
    ))
    config_dir = os.path.dirname(config_path)
    data_root = values.get("TELEPOST_DATA_ROOT")
    if not data_root:
        data_root = (
            os.path.dirname(config_dir)
            if os.path.basename(config_dir) == "pixivflow"
            else "data"
        )
    data_root = os.path.abspath(data_root)

    storage = {}
    try:
        with open(config_path, encoding="utf-8") as handle:
            storage = (json.load(handle).get("storage") or {})
    except (FileNotFoundError, OSError, ValueError, TypeError):
        pass

    def resolve_config_path(value: str, fallback: str) -> str:
        candidate = value or fallback
        return (
            candidate
            if os.path.isabs(candidate)
            else os.path.join(config_dir, candidate)
        )

    cache_path = resolve_config_path(storage.get("downloadDirectory"), "cache")
    database_path = resolve_config_path(storage.get("databasePath"), "pixivflow.db")
    outbox_path = os.path.join(os.path.dirname(database_path), "delivery-outbox")
    uploads_path = os.path.join(data_root, "api_uploads")

    result = {
        "pixivflow_cache": _directory_metrics(cache_path),
        "delivery_outbox": _outbox_metrics(outbox_path),
        "api_uploads": _directory_metrics(uploads_path),
        "review_queue": _review_queue_metrics(values),
    }
    try:
        usage = shutil.disk_usage(data_root)
        result["volume"] = {
            "total_mb": round(usage.total / 1048576, 1),
            "used_mb": round(usage.used / 1048576, 1),
            "available_mb": round(usage.free / 1048576, 1),
            "used_percent": round(usage.used * 100 / usage.total, 1),
        }
    except (FileNotFoundError, OSError, ZeroDivisionError):
        pass

    if use_cache:
        _STORAGE_METRICS_CACHE = {"expires_at": now + 15.0, "value": result}
    return result


def pixivflow_enabled(env=None) -> bool:
    env = env or os.environ
    return env.get("PIXIVFLOW_ENABLED", "false").strip().lower() in {
        "1", "true", "yes", "on"
    }


def prepare_pixivflow_env(base: dict) -> tuple[list, dict]:
    """Build the isolated command/environment for the optional PixivFlow worker."""
    config_path = base.get(
        "PIXIVFLOW_CONFIG", "/app/data/pixivflow/config.json"
    )
    template_path = base.get(
        "PIXIVFLOW_CONFIG_TEMPLATE",
        "/opt/pixivflow/node_modules/pixivflow/config/fly-two-bots.example.json",
    )
    os.makedirs(os.path.dirname(config_path), exist_ok=True)
    if not os.path.exists(config_path):
        if not os.path.exists(template_path):
            raise FileNotFoundError(
                f"PixivFlow config not found: {config_path}; template missing: {template_path}"
            )
        shutil.copy2(template_path, config_path)
        print(f"[supervisor] initialized PixivFlow config: {config_path}", flush=True)

    env = dict(base)
    # PixivFlow only needs its own refresh/submission tokens. Do not expose the
    # Telegram Bot API tokens to the Node child process.
    for key in [k for k in env if k.startswith("BOT") and k.endswith("_TOKEN")]:
        env.pop(key, None)
    env["PIXIV_DOWNLOADER_CONFIG"] = config_path
    command = shlex.split(base.get("PIXIVFLOW_COMMAND", "pixivflow scheduler"))
    if not command:
        raise ValueError("PIXIVFLOW_COMMAND cannot be empty")
    return command, env


def bot_webhook_port(index: int) -> int:
    return ROUTER_DEFAULT_PORT + index


def bot_webhook_path(index: int) -> str:
    return f"/webhook/bot{index}"


def bot_indices(env) -> list:
    """返回存在的 bot 序号列表（BOT1_TOKEN、BOT2_TOKEN…）"""
    indices = []
    i = 1
    while env.get(f"BOT{i}_TOKEN"):
        indices.append(i)
        i += 1
    return indices


def build_bot_env(index: int, base: dict) -> dict:
    """
    为第 index 个 bot 构建子进程环境：
    BOT{n}_X 覆盖 X；未覆盖的数据目录默认隔离到 data/bot{n}/ 下。
    WEBHOOK 模式下额外分配独立端口与回调路径（/webhook/botN）。
    """
    env = dict(base)
    # Identify the primary child explicitly. Process-wide maintenance such as
    # PixivFlow VACUUM must run once, not once per Bot against the same DB.
    env["TELEPOST_BOT_INDEX"] = str(index)
    env["TELEPOST_PRIMARY_BOT"] = "true" if index == 1 else "false"
    env["TOKEN"] = env.get(f"BOT{index}_TOKEN", "")
    env["CHANNEL_ID"] = env.get(f"BOT{index}_CHANNEL_ID", "")

    for key in OVERRIDABLE_KEYS:
        value = env.get(f"BOT{index}_{key}")
        if value:
            env[key] = value

    requested_mode = (
        env.get(f"BOT{index}_RUN_MODE")
        or env.get("RUN_MODE_REQUESTED")
        or env.get("RUN_MODE", "AUTO")
    )
    env["RUN_MODE_REQUESTED"] = requested_mode.strip().upper()
    env["RUN_MODE"] = resolve_run_mode(requested_mode, env.get("WEBHOOK_URL", ""))

    # 数据目录默认按 bot 隔离，避免不同频道的用户/帖子数据混在一起
    env["DB_PATH"] = env.get("DB_PATH", f"data/bot{index}/submissions.db")
    env["SEARCH_INDEX_DIR"] = env.get("SEARCH_INDEX_DIR", f"data/bot{index}/search_index")
    # 父路由固定占 8080；子进程在两种模式下都使用 8081/8082/...，
    # 这样 /api/botN/v1 路径可以在 Polling 与 Webhook 间保持不变。
    env["HEALTH_PORT"] = env.get(
        f"BOT{index}_HEALTH_PORT", str(bot_webhook_port(index))
    )

    # 多 bot webhook 模式：每个 bot 独占一个内部端口与回调路径
    if env.get("RUN_MODE", "").upper() == "WEBHOOK":
        env["WEBHOOK_PORT"] = str(bot_webhook_port(index))
        env["WEBHOOK_PATH"] = bot_webhook_path(index)
        base_url = (env.get("WEBHOOK_URL") or "").rstrip("/")
        # 允许父级 WEBHOOK_URL 带路径，但多 bot 时统一以 /webhook/botN 为准
        base_url = re.sub(r"/webhook/bot\d+$", "", base_url)
        if base_url:
            env["WEBHOOK_URL"] = base_url

    # 清掉所有 BOT*_TOKEN，防止子进程误读其他 bot 的凭据
    for key in [k for k in env if k.startswith("BOT") and k.endswith("_TOKEN")]:
        env.pop(key, None)
    return env


def run_single():
    """单 bot：原地替换为 main.py（信号直达主进程）"""
    os.execv(sys.executable, [sys.executable, "-u", "main.py"])


def build_router_app(indices: list):
    """
    多 bot HTTP 路由（aiohttp）：
    - GET /health        → 200（Fly 健康检查 / auto_stop 唤醒入口）
    - /webhook/botN(/**) → 转发到 127.0.0.1:(8080+N) 对应子进程
    """
    from aiohttp import ClientSession, ClientTimeout, web
    # NOTE: do not use web.AppKey here — its module-name resolution fails with
    # UnboundLocalError when build_router_app runs from a daemon thread context.
    # aiohttp Application is a plain dict; a string key is fully compatible.
    session_key = "telepost.router.session"

    async def client_session_context(app):
        timeout = ClientTimeout(
            total=float(os.environ.get("ROUTER_TIMEOUT_SECONDS", "300"))
        )
        app[session_key] = ClientSession(timeout=timeout, auto_decompress=False)
        yield
        await app[session_key].close()

    async def health(request):
        payload = {"status": "ok", "service": "telepost", "bots": indices}
        try:
            import glob
            import psutil
            procs = []
            for f in glob.glob("/proc/[0-9]*/status"):
                name = rss = None
                for line in open(f):
                    if line.startswith("Name:"):
                        name = line.split(":", 1)[1].strip()
                    elif line.startswith("VmRSS:"):
                        rss = int(line.split()[1])
                if rss and name in {"python", "node"}:
                    procs.append({"name": name, "rss_mb": round(rss / 1024.0, 1)})
            payload["process_rss"] = sorted(
                procs, key=lambda item: item["rss_mb"], reverse=True
            )
            payload["python_rss_mb"] = sorted(
                (item["rss_mb"] for item in procs if item["name"] == "python"),
                reverse=True,
            )
            payload["system_available_mb"] = round(
                psutil.virtual_memory().available / 1048576, 1
            )
        except Exception:
            pass
        try:
            payload["storage"] = await asyncio.to_thread(storage_health_snapshot)
        except Exception:
            pass
        return web.json_response(payload)

    # The router never buffers submission bodies. The explicit size ceiling is
    # still useful for malformed clients and matches TelePost's 10 x 50 MiB API.
    app = web.Application(client_max_size=ROUTER_CLIENT_MAX_BYTES)
    app.cleanup_ctx.append(client_session_context)
    app.router.add_get("/health", health)
    def make_relay(index: int, strip: str | None, prepend: str = ""):
        async def relay(request):
            path_qs = request.path_qs
            if strip and path_qs.startswith(strip):
                path_qs = path_qs[len(strip):] or "/"
            if prepend:
                path_qs = prepend + path_qs
            target = f"http://127.0.0.1:{bot_webhook_port(index)}{path_qs}"
            headers = {
                k: v for k, v in request.headers.items()
                if k.lower() not in {
                    "host", "content-length", "connection", "keep-alive",
                    "proxy-authenticate", "proxy-authorization", "te",
                    "trailer", "transfer-encoding", "upgrade",
                }
            }
            downstream = None
            try:
                session = request.app[session_key]
                async with session.request(
                    request.method,
                    target,
                    data=(
                        request.content.iter_chunked(65536)
                        if request.can_read_body
                        else None
                    ),
                    headers=headers,
                ) as resp:
                    response_headers = {
                        k: v for k, v in resp.headers.items()
                        if k.lower() not in {
                            "content-length", "connection", "keep-alive",
                            "proxy-authenticate", "proxy-authorization", "te",
                            "trailer", "transfer-encoding", "upgrade",
                        }
                    }
                    downstream = web.StreamResponse(
                        status=resp.status,
                        reason=resp.reason,
                        headers=response_headers,
                    )
                    await downstream.prepare(request)
                    async for chunk in resp.content.iter_chunked(65536):
                        await downstream.write(chunk)
                    await downstream.write_eof()
                    return downstream
            except Exception as exc:
                if downstream is not None and downstream.prepared:
                    raise
                return web.json_response({"ok": False, "error": str(exc)}, status=502)
        return relay

    for index in indices:
        webhook_relay = make_relay(index, None)
        app.router.add_route("*", bot_webhook_path(index), webhook_relay)
        app.router.add_route("*", bot_webhook_path(index) + "/{tail:.*}", webhook_relay)
        # HTTP API：/api/botN/v1/* → 子进程 /v1/*（投稿含文件上传，超时放宽到 120s）
        api_prefix = f"/api/bot{index}"
        api_relay = make_relay(index, api_prefix, prepend="/api")
        app.router.add_route("*", api_prefix, api_relay)
        app.router.add_route("*", api_prefix + "/{tail:.*}", api_relay)
    return app


def start_webhook_router(port: int, indices: list):
    """在守护线程里启动路由进程（独立事件循环，与子进程互不干扰）"""

    def _serve():
        loop = __import__("asyncio").new_event_loop()
        __import__("asyncio").set_event_loop(loop)
        from aiohttp import web

        async def _run():
            app = build_router_app(indices)
            runner = web.AppRunner(app)
            await runner.setup()
            site = web.TCPSite(runner, "0.0.0.0", port)
            await site.start()
            print(
                f"[router] HTTP 路由已启动 :{port} → "
                + ", ".join(f"bot{i}:{bot_webhook_port(i)}" for i in indices),
                flush=True,
            )
            while True:
                await __import__("asyncio").sleep(3600)

        loop.run_until_complete(_run())

    thread = threading.Thread(target=_serve, name="webhook-router", daemon=True)
    thread.start()
    return thread


def run_multi(indices: list) -> None:
    """多 bot：监督循环，维持每个 bot 各一个子进程"""
    processes = {}
    pixivflow_process = {"proc": None}
    stopping = {"flag": False}

    def _handle_stop(signum, frame):
        stopping["flag"] = True
        for proc in processes.values():
            if proc.poll() is None:
                proc.terminate()
        worker = pixivflow_process["proc"]
        if worker is not None and worker.poll() is None:
            worker.terminate()

    signal.signal(signal.SIGTERM, _handle_stop)
    signal.signal(signal.SIGINT, _handle_stop)

    # 提前创建各 bot 的数据目录
    for index in indices:
        env = build_bot_env(index, os.environ)
        for directory in (os.path.dirname(env["DB_PATH"]), env["SEARCH_INDEX_DIR"]):
            if directory:
                os.makedirs(directory, exist_ok=True)

    # 两种 Telegram 更新模式都启动统一入口。Webhook 使用 /webhook/botN；
    # Polling 不使用这些回调，但 PixivFlow 仍可稳定调用 /api/botN/v1。
    router_port = int(os.environ.get("WEBHOOK_PORT", str(ROUTER_DEFAULT_PORT)))
    start_webhook_router(router_port, indices)

    pixivflow_spec = None
    if pixivflow_enabled():
        pixivflow_spec = prepare_pixivflow_env(dict(os.environ))

    print(f"[supervisor] 多 bot 模式启动，共 {len(indices)} 个 bot: {indices}", flush=True)
    while not stopping["flag"]:
        for index in indices:
            proc = processes.get(index)
            if proc is not None and proc.poll() is None:
                continue
            if proc is not None:
                print(f"[supervisor] bot{index} 异常退出 (code={proc.returncode})，5 秒后重启", flush=True)
                time.sleep(5)
                if stopping["flag"]:
                    break
            env = build_bot_env(index, os.environ)
            processes[index] = subprocess.Popen([sys.executable, "-u", "main.py"], env=env)
            print(f"[supervisor] bot{index} 已启动 (pid={processes[index].pid})", flush=True)

        if pixivflow_spec is not None and not stopping["flag"]:
            worker = pixivflow_process["proc"]
            if worker is None or worker.poll() is not None:
                if worker is not None:
                    print(
                        f"[supervisor] PixivFlow 异常退出 (code={worker.returncode})，5 秒后重启",
                        flush=True,
                    )
                    time.sleep(5)
                if not stopping["flag"]:
                    command, worker_env = pixivflow_spec
                    worker = subprocess.Popen(command, env=worker_env)
                    pixivflow_process["proc"] = worker
                    print(f"[supervisor] PixivFlow 已启动 (pid={worker.pid})", flush=True)
        time.sleep(5)

    for proc in processes.values():
        try:
            proc.wait(timeout=15)
        except subprocess.TimeoutExpired:
            proc.kill()
    worker = pixivflow_process["proc"]
    if worker is not None:
        try:
            worker.wait(timeout=15)
        except subprocess.TimeoutExpired:
            worker.kill()
    print("[supervisor] 已全部停止", flush=True)


def main():
    indices = bot_indices(os.environ)
    if indices:
        # 多 bot 的路由器必须先知道最终模式，因此在启动子进程前解析。
        # 单 bot 不在此处解析，避免环境变量覆盖 config.ini 中的配置。
        requested_mode = os.environ.get("RUN_MODE", "AUTO")
        os.environ["RUN_MODE_REQUESTED"] = requested_mode.strip().upper()
        os.environ["RUN_MODE"] = resolve_run_mode(
            requested_mode, os.environ.get("WEBHOOK_URL", "")
        )
        if os.environ["RUN_MODE_REQUESTED"] == "AUTO":
            print(
                f"[launcher] AUTO 选择 {os.environ['RUN_MODE']} 模式",
                flush=True,
            )
        run_multi(indices)
    else:
        run_single()


if __name__ == "__main__":
    main()
