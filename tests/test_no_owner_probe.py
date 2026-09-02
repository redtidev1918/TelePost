"""频道维护不得向 OWNER 私聊发送临时消息。"""

import inspect
from pathlib import Path

from handlers import channel_listener, stats_handlers


def test_channel_maintenance_does_not_forward_messages():
    for module in (channel_listener, stats_handlers):
        assert ".forward_message(" not in inspect.getsource(module)

    diagnostic = Path(__file__).parents[1] / "diagnose_stats.py"
    assert ".forward_message(" not in diagnostic.read_text(encoding="utf-8")
