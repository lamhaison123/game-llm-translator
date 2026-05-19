from __future__ import annotations

import csv
from datetime import datetime, timezone
from pathlib import Path

from .app_config import app_data_dir
from .models import TranslationResult

MEMORY_FIELDS = ["source", "target", "source_lang", "target_lang", "provider", "context", "updated_at"]


def global_memory_path() -> Path:
    return app_data_dir() / "translation_memory.csv"


def load_memory(paths: list[Path], target_lang: str, source_lang: str | None = None) -> dict[str, str]:
    memory: dict[str, str] = {}
    wanted_target = target_lang.strip().lower()
    wanted_source = (source_lang or "auto").strip().lower()
    for path in paths:
        if not path.exists():
            continue
        with path.open("r", newline="", encoding="utf-8-sig") as fp:
            for row in csv.DictReader(fp):
                source = row.get("source", "")
                target = row.get("target", "")
                row_target = row.get("target_lang", "").strip().lower()
                row_source = row.get("source_lang", "auto").strip().lower()
                if not source.strip() or not target.strip():
                    continue
                if row_target and row_target != wanted_target:
                    continue
                if row_source not in {"", "auto", wanted_source}:
                    continue
                memory[source] = target
    return memory


def save_memory(path: Path, results: list[TranslationResult], target_lang: str, source_lang: str | None, provider: str) -> int:
    path.parent.mkdir(parents=True, exist_ok=True)
    rows: dict[tuple[str, str], dict[str, str]] = {}
    if path.exists():
        with path.open("r", newline="", encoding="utf-8-sig") as fp:
            for row in csv.DictReader(fp):
                source = row.get("source", "")
                row_target = row.get("target_lang", "")
                if source and row_target:
                    rows[(source, row_target.strip().lower())] = {name: row.get(name, "") for name in MEMORY_FIELDS}
    updated_at = datetime.now(timezone.utc).isoformat()
    saved = 0
    for result in results:
        if not result.source.strip() or not result.target.strip() or result.target == result.source:
            continue
        key = (result.source, target_lang.strip().lower())
        rows[key] = {
            "source": result.source,
            "target": result.target,
            "source_lang": source_lang or "auto",
            "target_lang": target_lang,
            "provider": provider,
            "context": result.context,
            "updated_at": updated_at,
        }
        saved += 1
    with path.open("w", newline="", encoding="utf-8") as fp:
        writer = csv.DictWriter(fp, fieldnames=MEMORY_FIELDS)
        writer.writeheader()
        for row in rows.values():
            writer.writerow(row)
    return saved
