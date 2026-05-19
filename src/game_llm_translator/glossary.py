from __future__ import annotations

import csv
from pathlib import Path


def load_glossary(path: Path | None) -> list[tuple[str, str]]:
    """Load (term, translation) pairs from a CSV with columns: term, translation, [note].

    Returns an empty list if path is None, the file doesn't exist, or it cannot be parsed.
    Rows missing term or translation are skipped silently.
    """
    if path is None or not path.exists():
        return []
    entries: list[tuple[str, str]] = []
    seen: set[str] = set()
    try:
        with path.open("r", newline="", encoding="utf-8-sig") as fp:
            reader = csv.DictReader(fp)
            for row in reader:
                term = (row.get("term") or "").strip()
                translation = (row.get("translation") or "").strip()
                if not term or not translation:
                    continue
                if term in seen:
                    continue
                seen.add(term)
                entries.append((term, translation))
    except (OSError, csv.Error, UnicodeDecodeError):
        return []
    return entries


def format_glossary_for_prompt(entries: list[tuple[str, str]], max_chars: int = 4000) -> str:
    """Render glossary entries as a system prompt addon block.

    Truncates gracefully when total exceeds max_chars; appends a note if truncated.
    Returns an empty string if entries is empty.
    """
    if not entries:
        return ""
    header = "## Glossary (use these EXACT translations for these terms; never substitute)\n"
    lines: list[str] = []
    used = len(header)
    truncated = 0
    for term, translation in entries:
        line = f'- "{term}" -> "{translation}"\n'
        if used + len(line) > max_chars:
            truncated = len(entries) - len(lines)
            break
        lines.append(line)
        used += len(line)
    block = header + "".join(lines)
    if truncated:
        block += f"(...{truncated} more terms omitted to keep prompt short)\n"
    return block
