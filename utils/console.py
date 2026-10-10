"""Use UTF-8 for Windows CLI streams, including PyInstaller's frozen runtime."""
import os
import sys


def configure_windows_stdio() -> None:
    # Frozen interpreters may ignore PYTHONIOENCODING. Reconfigure the streams
    # directly, and propagate the encoding to source-mode bot child processes.
    if os.name != "nt":
        return
    os.environ["PYTHONIOENCODING"] = "utf-8"
    for stream in (sys.stdin, sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if callable(reconfigure):
            reconfigure(encoding="utf-8", errors=getattr(stream, "errors", None) or "strict")
