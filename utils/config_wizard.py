"""首次运行配置向导（仅标准库，供源码 python run.py --setup 与冻结版共用）。

与 config/settings.py 的目录规则一致：冻结时读写 exe 同目录的 config.ini，
源码运行时读写仓库根。不得 import 任何业务模块（settings 在缺 TOKEN 时
import 即抛错，向导必须在它之前运行）。
"""
import configparser
import os
import sys


def app_root() -> str:
    """config.ini / data / logs 的锚点目录。"""
    if getattr(sys, "frozen", False):
        return os.path.dirname(os.path.abspath(sys.executable))
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def config_path() -> str:
    return os.path.join(app_root(), "config.ini")


def _config_says_ready() -> bool:
    try:
        parser = configparser.ConfigParser()
        parser.read(config_path())
        token = parser.get("BOT", "TOKEN", fallback="")
        channel = parser.get("BOT", "CHANNEL_ID", fallback="")
        return bool(token and token != "your_bot_token_here" and channel)
    except Exception:
        return False


def config_ready() -> bool:
    """有可用配置即可启动：环境变量优先，其次 exe 同目录的 config.ini。"""
    if os.environ.get("TOKEN") or os.environ.get("BOT1_TOKEN"):
        return True
    return _config_says_ready()


def write_config(path: str, *, token: str, channel: str, owner: str) -> None:
    """写入最小可运行配置（其余项用 settings 的默认值）。"""
    parser = configparser.ConfigParser()
    parser["BOT"] = {}
    parser["BOT"]["TOKEN"] = token.strip()
    parser["BOT"]["CHANNEL_ID"] = channel.strip()
    if owner and owner.strip():
        parser["BOT"]["OWNER_ID"] = owner.strip()
    with open(path, "w", encoding="utf-8") as handle:
        parser.write(handle)


def _ask(prompt: str) -> str:
    try:
        return input(prompt).strip()
    except (EOFError, KeyboardInterrupt):
        print("\n已取消。")
        sys.exit(1)


def run_wizard() -> None:
    """交互式引导：问三项必填，写 config.ini，提示再次运行启动。"""
    target = config_path()
    if os.path.exists(target) and _config_says_ready():
        answer = _ask(f"config.ini 已存在且可用（{target}），覆盖重写？(y/N) ").lower()
        if answer != "y":
            print("保持现有配置，结束。")
            return
    print("=" * 56)
    print("TelePost 首次运行配置（只需 3 项，其余用默认值）")
    print("=" * 56)
    token = _ask("① Bot Token（找 @BotFather 创建机器人获得，格式 123456:xxxx）: ")
    if ":" not in token:
        print("❌ Token 格式不对，应形如 123456789:AA... 请重新运行 --setup。")
        sys.exit(1)
    channel = _ask("② 投稿频道（@频道名 或 -100xxxxxxxxxx）: ")
    if not channel:
        print("❌ 频道不能为空，请重新运行 --setup。")
        sys.exit(1)
    owner = _ask("③ 你的 Telegram User ID（可选，回车跳过）: ")
    write_config(target, token=token, channel=channel, owner=owner)
    print()
    print(f"✅ 已写入 {target}")
    print("再次运行即可启动机器人（配置在 exe 同目录的 config.ini，可随时改）。")


if __name__ == "__main__":
    run_wizard()
