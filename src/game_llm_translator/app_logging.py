from __future__ import annotations

import threading
from datetime import datetime
from pathlib import Path

from .app_config import app_data_dir

_log_lock = threading.Lock()
_api_log_lock = threading.Lock()

_api_logging_enabled: bool = False


def logs_dir() -> Path:
    return app_data_dir() / "logs"


def log_file_path() -> Path:
    return logs_dir() / f"{datetime.now().strftime('%Y-%m-%d')}.log"


def api_log_file_path() -> Path:
    return logs_dir() / f"{datetime.now().strftime('%Y-%m-%d')}-api-debug.log"


def set_api_logging(enabled: bool) -> None:
    global _api_logging_enabled
    _api_logging_enabled = enabled


def is_api_logging_enabled() -> bool:
    return _api_logging_enabled


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
        pass


def log_api_call(
    provider: str,
    direction: str,
    content: str,
    *,
    batch_index: int | None = None,
    entry_count: int | None = None,
) -> None:
    """Append a full API request or response to the daily api-debug log.

    Only writes when api logging is enabled (set_api_logging(True)).
    direction: 'REQUEST' or 'RESPONSE'
    """
    if not _api_logging_enabled:
        return
    try:
        path = api_log_file_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        timestamp = datetime.now().isoformat(timespec="milliseconds")
        meta = f"provider={provider}"
        if batch_index is not None:
            meta += f" batch={batch_index}"
        if entry_count is not None:
            meta += f" entries={entry_count}"
        header = f"\n{'='*80}\n{timestamp} [{direction}] {meta}\n{'='*80}\n"
        with _api_log_lock:
            with path.open("a", encoding="utf-8") as fp:
                fp.write(header)
                fp.write(content)
                fp.write("\n")
    except Exception:
        pass
