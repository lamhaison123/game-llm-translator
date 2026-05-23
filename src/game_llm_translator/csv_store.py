from __future__ import annotations

import csv
import os
import tempfile
import threading
from pathlib import Path
from typing import Callable, IO

from .models import TextEntry, TranslationResult


_WRITE_LOCK = threading.Lock()


def _atomic_write_text(path: Path, write_fn: Callable[[IO[str]], None]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=path.name + ".", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", newline="", encoding="utf-8") as fp:
            write_fn(fp)
        with _WRITE_LOCK:
            os.replace(tmp, path)
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def save_entries(entries: list[TextEntry], file: Path) -> None:
    def _write(fp: IO[str]) -> None:
        writer = csv.DictWriter(fp, fieldnames=["file", "key", "source", "context", "context_text"])
        writer.writeheader()
        for entry in entries:
            writer.writerow({"file": str(entry.file), "key": entry.key, "source": entry.source, "context": entry.context, "context_text": entry.context_text})
    _atomic_write_text(file, _write)


def load_entries(file: Path) -> list[TextEntry]:
    try:
        with file.open("r", newline="", encoding="utf-8-sig") as fp:
            return [TextEntry(Path(row["file"]), row["key"], row["source"], row.get("context", ""), row.get("context_text", "")) for row in csv.DictReader(fp) if row.get("file") and row.get("key")]
    except (OSError, UnicodeDecodeError, csv.Error, KeyError) as exc:
        raise ValueError(f"Failed to load entries from {file}: {exc}") from exc


def save_results(results: list[TranslationResult], file: Path) -> None:
    def _write(fp: IO[str]) -> None:
        writer = csv.DictWriter(fp, fieldnames=["file", "key", "source", "target", "context", "sub_keys"])
        writer.writeheader()
        for item in results:
            writer.writerow({"file": str(item.file), "key": item.key, "source": item.source, "target": item.target, "context": item.context, "sub_keys": "\x1f".join(item.sub_keys) if item.sub_keys else ""})
    _atomic_write_text(file, _write)


def load_results(file: Path) -> list[TranslationResult]:
    try:
        with file.open("r", newline="", encoding="utf-8-sig") as fp:
            return [
                TranslationResult(Path(row["file"]), row["key"], row["source"], row["target"], row.get("context", ""), sub_keys=row.get("sub_keys", "").split("\x1f") if row.get("sub_keys", "") else [])
                for row in csv.DictReader(fp)
                if row.get("file") and row.get("key") and row.get("source") is not None and row.get("target") is not None
            ]
    except (OSError, UnicodeDecodeError, csv.Error, KeyError) as exc:
        raise ValueError(f"Failed to load results from {file}: {exc}") from exc
