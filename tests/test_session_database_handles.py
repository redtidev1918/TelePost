"""Storage errors must release Windows file handles before returning."""
import sqlite3

import pytest

from utils import database


@pytest.mark.parametrize("operation,args", [
    ("initialize_database", ()),
    ("get_user_state", (1,)),
    ("save_user_state", (1, "MEDIA", {})),
    ("update_user_activity", (1,)),
    ("delete_user_state", (1,)),
    ("cleanup_expired_sessions", ()),
    ("get_all_user_states", ()),
    ("get_all_active_users", ()),
])
@pytest.mark.parametrize("failure_at", ["configuration", "query"])
def test_session_error_closes_connection_immediately(tmp_path, monkeypatch, operation, args, failure_at):
    path = tmp_path / "sessions.db"
    monkeypatch.setattr(database, "SESSION_DB_PATH", str(path))
    opened = []
    connect = sqlite3.connect

    class FailingConnection(sqlite3.Connection):
        closed = False

        def execute(self, *args, **kwargs):
            if failure_at == "configuration":
                raise sqlite3.OperationalError("simulated configuration failure")
            return super().execute(*args, **kwargs)

        def cursor(self, *args, **kwargs):
            raise sqlite3.OperationalError("simulated query failure")

        def close(self):
            super().close()
            self.closed = True

    def tracked_connect(*args, **kwargs):
        connection = connect(*args, factory=FailingConnection, **kwargs)
        opened.append(connection)  # Keep references: GC must not mask a leaked handle.
        return connection

    monkeypatch.setattr(database.sqlite3, "connect", tracked_connect)
    getattr(database, operation)(*args)
    assert len(opened) == 1 and opened[0].closed
    path.unlink()  # A leaked SQLite handle raises WinError 32 here.
