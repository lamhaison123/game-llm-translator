from __future__ import annotations

from datetime import datetime
from pathlib import Path


def timestamped_unique_path(parent: Path, prefix: str, suffix: str = "", when: datetime | None = None) -> Path:
    """Return a timestamp-based child path, adding a numeric suffix on collision.

    The first candidate is ``{prefix}{YYYYmmdd_HHMMSS}{suffix}``. If that path
    already exists, ``_1``, ``_2``, ... are inserted before ``suffix`` until a
    free path is found.
    """
    stamp = (when or datetime.now()).strftime("%Y%m%d_%H%M%S")
    base_name = f"{prefix}{stamp}"
    candidate = parent / f"{base_name}{suffix}"
    index = 1
    while candidate.exists():
        candidate = parent / f"{base_name}_{index}{suffix}"
        index += 1
    return candidate
