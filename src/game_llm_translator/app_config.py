from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

APP_DIR_NAME = "game-llm-translator"
CONFIG_FILE_NAME = "config.json"


def app_data_dir() -> Path:
    root = os.getenv("APPDATA")
    if root:
        return Path(root) / APP_DIR_NAME
    return Path.home() / f".{APP_DIR_NAME}"


def config_path() -> Path:
    return app_data_dir() / CONFIG_FILE_NAME


def load_app_config() -> dict[str, Any]:
    path = config_path()
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    return data if isinstance(data, dict) else {}


def save_app_config(data: dict[str, Any]) -> None:
    # WARNING: the api_key field is stored as plaintext on disk.
    # Do not commit config.json or share it with others.
    path = config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
