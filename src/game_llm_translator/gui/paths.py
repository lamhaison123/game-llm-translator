from __future__ import annotations

from pathlib import Path


def normalize_path_text(value: str) -> str:
    """Return ``value`` with the native OS path separator.

    Qt's QFileDialog returns forward slashes on Windows regardless of OS.
    Round-tripping through ``Path`` switches to the native separator so the
    QLineEdit display, persisted config, and any later string comparisons
    all see the same form.
    """
    text = (value or "").strip()
    return str(Path(text)) if text else ""
