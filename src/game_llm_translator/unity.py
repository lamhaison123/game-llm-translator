from __future__ import annotations

import csv
import json
from pathlib import Path

from .models import TextEntry, TranslationResult

UNITY_TEXT_EXTS = {".csv", ".tsv", ".json", ".txt"}
UNITY_SKIP_DIRS = {"Library", "Temp", "Logs", "obj", "bin", "Packages", "ProjectSettings", "UserSettings", "node_modules"}
UNITY_SKIP_SUFFIXES = {".meta", ".asmdef", ".cs", ".dll", ".png", ".jpg", ".asset"}


def _looks_like_path_string(value: str) -> bool:
    return "/" in value or "\\" in value


def extract_unity(game_dir: Path) -> list[TextEntry]:
    entries: list[TextEntry] = []
    for file in game_dir.rglob("*"):
        if file.suffix.lower() not in UNITY_TEXT_EXTS:
            continue
        if file.suffix.lower() in UNITY_SKIP_SUFFIXES:
            continue
        if any(part in UNITY_SKIP_DIRS for part in file.parts):
            continue
        try:
            text = file.read_text(encoding="utf-8-sig")
        except UnicodeDecodeError:
            continue
        if file.suffix.lower() in {".csv", ".tsv"}:
            dialect = "excel-tab" if file.suffix.lower() == ".tsv" else "excel"
            rows = list(csv.DictReader(text.splitlines(), dialect=dialect))
            for row_index, row in enumerate(rows):
                for col, value in row.items():
                    if value and len(value.strip()) > 1:
                        entries.append(TextEntry(file, f"row:{row_index}:col:{col}", value, "unity_table"))
        elif file.suffix.lower() == ".json":
            try:
                data = json.loads(text)
            except Exception:
                continue
            entries.extend(_walk_json(data, file))
        else:
            for line_no, line in enumerate(text.splitlines()):
                if line.strip() and len(line.strip()) > 1:
                    entries.append(TextEntry(file, f"line:{line_no}", line, "unity_text"))
    return entries


def _walk_json(value, file: Path, prefix: str = "$") -> list[TextEntry]:
    entries: list[TextEntry] = []
    if isinstance(value, dict):
        for key, child in value.items():
            child_key = f"{prefix}.{key}"
            if isinstance(child, str) and child.strip() and len(child.strip()) > 1:
                if not _looks_like_path_string(child):
                    entries.append(TextEntry(file, child_key, child, "unity_json"))
            else:
                entries.extend(_walk_json(child, file, child_key))
    elif isinstance(value, list):
        for index, child in enumerate(value):
            entries.extend(_walk_json(child, file, f"{prefix}[{index}]"))
    return entries
