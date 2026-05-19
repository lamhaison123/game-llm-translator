from __future__ import annotations

import csv
from pathlib import Path

from .models import TextEntry, TranslationResult


def save_entries(entries: list[TextEntry], file: Path) -> None:
    file.parent.mkdir(parents=True, exist_ok=True)
    with file.open("w", newline="", encoding="utf-8") as fp:
        writer = csv.DictWriter(fp, fieldnames=["file", "key", "source", "context", "context_text"])
        writer.writeheader()
        for entry in entries:
            writer.writerow({"file": str(entry.file), "key": entry.key, "source": entry.source, "context": entry.context, "context_text": entry.context_text})


def load_entries(file: Path) -> list[TextEntry]:
    with file.open("r", newline="", encoding="utf-8-sig") as fp:
        return [TextEntry(Path(row["file"]), row["key"], row["source"], row.get("context", ""), row.get("context_text", "")) for row in csv.DictReader(fp)]


def save_results(results: list[TranslationResult], file: Path) -> None:
    file.parent.mkdir(parents=True, exist_ok=True)
    with file.open("w", newline="", encoding="utf-8") as fp:
        writer = csv.DictWriter(fp, fieldnames=["file", "key", "source", "target", "context"])
        writer.writeheader()
        for item in results:
            writer.writerow({"file": str(item.file), "key": item.key, "source": item.source, "target": item.target, "context": item.context})


def load_results(file: Path) -> list[TranslationResult]:
    with file.open("r", newline="", encoding="utf-8-sig") as fp:
        return [
            TranslationResult(Path(row["file"]), row["key"], row["source"], row["target"], row.get("context", ""))
            for row in csv.DictReader(fp)
        ]
