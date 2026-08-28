"""
TelePost 启动器

- 未设置 BOT1_TOKEN：单 bot 模式，直接运行 main.py（行为与旧版完全一致）
- 设置了 BOT1_TOKEN：多 bot 模式，为每个 bot（BOT1、BOT2、…）派生独立子进程，
  各自使用独立的 TOKEN/CHANNEL_ID/OWNER_ID 与独立数据目录（data/botN/），
  一台机器承载多个频道，节省开销。

任一子进程异常退出时自动重启（5 秒退避）；SIGTERM/SIGINT 会转发给所有子进程。
"""
import os
import re
import signal
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
    # 多进程共存时健康检查端口错开（8080/8081/…），避免端口冲突（仅 Polling 模式使用）
    env["HEALTH_PORT"] = env.get("HEALTH_PORT", str(8079 + index))

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
    多 bot webhook 路由（aiohttp）：
    - GET /health        → 200（Fly 健康检查 / auto_stop 唤醒入口）
    - /webhook/botN(/**) → 转发到 127.0.0.1:(8080+N) 对应子进程
    """
    from aiohttp import ClientSession, web

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
                if rss and name == "python":
                    procs.append(rss)
            payload["python_rss_mb"] = sorted(
                (round(x / 1024.0, 1) for x in procs), reverse=True
            )
            payload["system_available_mb"] = round(
                psutil.virtual_memory().available / 1048576, 1
            )
        except Exception:
            pass
        return web.json_response(payload)

    app = web.Application()
    app.router.add_get("/health", health)
    def make_relay(index: int, strip: str | None, prepend: str = ""):
        async def relay(request):
            path_qs = request.path_qs
            if strip and path_qs.startswith(strip):
                path_qs = path_qs[len(strip):] or "/"
            if prepend:
                path_qs = prepend + path_qs
            target = f"http://127.0.0.1:{bot_webhook_port(index)}{path_qs}"
            body = await request.read()
            headers = {
                k: v for k, v in request.headers.items()
                if k.lower() not in ("host", "content-length")
            }
            try:
                async with ClientSession() as session:
                    async with session.request(
                        request.method, target, data=body, headers=headers,
                        timeout=__import__("aiohttp").ClientTimeout(total=120),
                    ) as resp:
                        data = await resp.read()
                        return web.Response(
                            status=resp.status, body=data,
                            content_type=resp.content_type or "application/json",
                        )
            except Exception as exc:
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
            print(f"[router] webhook 路由已启动 :{port} → " + ", ".join(f"bot{i}:8080+i" for i in indices), flush=True)
            while True:
                await __import__("asyncio").sleep(3600)

        loop.run_until_complete(_run())

    thread = threading.Thread(target=_serve, name="webhook-router", daemon=True)
    thread.start()
    return thread


def run_multi(indices: list) -> None:
    """多 bot：监督循环，维持每个 bot 各一个子进程"""
    processes = {}
    stopping = {"flag": False}

    def _handle_stop(signum, frame):
        stopping["flag"] = True
        for proc in processes.values():
            if proc.poll() is None:
                proc.terminate()

    signal.signal(signal.SIGTERM, _handle_stop)
    signal.signal(signal.SIGINT, _handle_stop)

    # 提前创建各 bot 的数据目录
    for index in indices:
        env = build_bot_env(index, os.environ)
        for directory in (os.path.dirname(env["DB_PATH"]), env["SEARCH_INDEX_DIR"]):
            if directory:
                os.makedirs(directory, exist_ok=True)

    # WEBHOOK 模式：启动按路径转发的路由（/webhook/botN → 子进程端口），
    # 配合 Fly 的 auto_stop，空闲停机、来消息自动唤醒，最大限度省钱
    if os.environ.get("RUN_MODE", "").upper() == "WEBHOOK":
        router_port = int(os.environ.get("WEBHOOK_PORT", str(ROUTER_DEFAULT_PORT)))
        start_webhook_router(router_port, indices)

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
        time.sleep(5)

    for proc in processes.values():
        try:
            proc.wait(timeout=15)
        except subprocess.TimeoutExpired:
            proc.kill()
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
