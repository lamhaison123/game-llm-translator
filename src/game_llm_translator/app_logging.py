from __future__ import annotations

import threading
from datetime import datetime
from pathlib import Path

from .app_config import app_data_dir

_log_lock = threading.Lock()


def logs_dir() -> Path:
    return app_data_dir() / "logs"


def log_file_path() -> Path:
    return logs_dir() / f"{datetime.now().strftime('%Y-%m-%d')}.log"


def log_event(message: str, level: str = "INFO") -> None:
    """Append a single log entry to the daily log file, thread-safely."""
    try:
        path = log_file_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        timestamp = datetime.now().isoformat(timespec="seconds")
        safe_message = message.replace("\r", " ").replace("\n", " ")
        with _log_lock:
            with path.open("a", encoding="utf-8") as fp:
                fp.write(f"{timestamp} [{level}] {safe_message}\n")
    except Exception:
        # Never let logging errors crash the caller
        pass
