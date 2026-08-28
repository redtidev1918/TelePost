"""
TelePost 启动器

- 未设置 BOT1_TOKEN：单 bot 模式，直接运行 main.py（行为与旧版完全一致）
- 设置了 BOT1_TOKEN：多 bot 模式，为每个 bot（BOT1、BOT2、…）派生独立子进程，
  各自使用独立的 TOKEN/CHANNEL_ID/OWNER_ID 与独立数据目录（data/botN/），
  一台机器承载多个频道，节省开销。

任一子进程异常退出时自动重启（5 秒退避）；SIGTERM/SIGINT 会转发给所有子进程。
"""
import os
import signal
import subprocess
import sys
import time

# 子进程可按 BOT{n}_<KEY> 覆盖的配置项
OVERRIDABLE_KEYS = (
    "OWNER_ID", "ADMIN_IDS", "SHOW_SUBMITTER", "NOTIFY_OWNER",
    "BOT_MODE", "ALLOWED_FILE_TYPES", "SUBMIT_LIMIT_PER_HOUR",
    "DB_PATH", "SEARCH_INDEX_DIR", "SEARCH_ENABLED", "SEARCH_ANALYZER",
    "HEALTH_PORT", "TIMEOUT", "RUN_MODE", "WEBHOOK_URL", "WEBHOOK_PATH",
    "WEBHOOK_PORT", "WEBHOOK_SECRET_TOKEN",
)


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
    """
    env = dict(base)
    env["TOKEN"] = env.get(f"BOT{index}_TOKEN", "")
    env["CHANNEL_ID"] = env.get(f"BOT{index}_CHANNEL_ID", "")

    for key in OVERRIDABLE_KEYS:
        value = env.get(f"BOT{index}_{key}")
        if value:
            env[key] = value

    # 数据目录默认按 bot 隔离，避免不同频道的用户/帖子数据混在一起
    env["DB_PATH"] = env.get("DB_PATH", f"data/bot{index}/submissions.db")
    env["SEARCH_INDEX_DIR"] = env.get("SEARCH_INDEX_DIR", f"data/bot{index}/search_index")
    # 多进程共存时健康检查端口错开（8080/8081/…），避免端口冲突
    env["HEALTH_PORT"] = env.get("HEALTH_PORT", str(8079 + index))

    # 清掉所有 BOT*_TOKEN，防止子进程误读其他 bot 的凭据
    for key in [k for k in env if k.startswith("BOT") and k.endswith("_TOKEN")]:
        env.pop(key, None)
    return env


def run_single():
    """单 bot：原地替换为 main.py（信号直达主进程）"""
    os.execv(sys.executable, [sys.executable, "-u", "main.py"])


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
        run_multi(indices)
    else:
        run_single()


if __name__ == "__main__":
    main()
