"""Diff two extraction CSVs (e.g. game v1.0 vs v1.1) and carry forward translations.

Typical use case: an RPG Maker game ships a patch. The user runs extract on the
new version and wants to know which entries are new, which had their source text
changed (and need re-translation), which are unchanged (translation carries
over), and which were removed from the game.

The output report.csv has the same shape as a translations CSV plus a `status`
column so the user can sort/filter:
  - unchanged: source identical, translation copied from old run
  - changed:   source differs, target left empty for re-translation
  - new:       entry not in old extraction, target empty
  - removed:   entry was in old extraction but not in new (kept for audit)
"""
from __future__ import annotations

import csv
from dataclasses import dataclass
from pathlib import Path
from typing import IO

from .csv_store import _atomic_write_text, load_entries, load_results
from .models import TextEntry, TranslationResult, text_identity


@dataclass(slots=True, frozen=True)
class DiffReport:
    unchanged: list[TranslationResult]
    changed: list[tuple[TextEntry, str]]   # (new entry, old target — for context)
    new: list[TextEntry]
    removed: list[TranslationResult]

    @property
    def stats(self) -> dict[str, int]:
        return {
            "unchanged": len(self.unchanged),
            "changed": len(self.changed),
            "new": len(self.new),
            "removed": len(self.removed),
        }


def diff_entries(
    old_entries: list[TextEntry],
    new_entries: list[TextEntry],
    old_translations: list[TranslationResult],
) -> DiffReport:
    """Compute the 4-way diff between old/new extractions and old translations."""
    old_by_id = {text_identity(e.file, e.key): e for e in old_entries}
    new_by_id = {text_identity(e.file, e.key): e for e in new_entries}
    old_tr_by_id = {text_identity(t.file, t.key): t for t in old_translations}

    unchanged: list[TranslationResult] = []
    changed: list[tuple[TextEntry, str]] = []
    new: list[TextEntry] = []
    removed: list[TranslationResult] = []

    for identity, new_entry in new_by_id.items():
        old_entry = old_by_id.get(identity)
        if old_entry is None:
            new.append(new_entry)
            continue
        if old_entry.source == new_entry.source:
            # Source unchanged — carry translation forward if we have one.
            old_tr = old_tr_by_id.get(identity)
            if old_tr is not None and old_tr.target.strip():
                unchanged.append(TranslationResult(
                    new_entry.file, new_entry.key, new_entry.source,
                    old_tr.target, new_entry.context, sub_keys=old_tr.sub_keys,
                ))
            else:
                # No prior translation — treat as new so user re-translates.
                new.append(new_entry)
        else:
            old_target = old_tr_by_id.get(identity)
            changed.append((new_entry, old_target.target if old_target else ""))

    for identity, old_tr in old_tr_by_id.items():
        if identity not in new_by_id:
            removed.append(old_tr)

    return DiffReport(unchanged=unchanged, changed=changed, new=new, removed=removed)


def write_diff_report(report: DiffReport, out: Path) -> None:
    """Write the diff as a translations-shaped CSV with an extra `status` column.

    Removed entries are written too so the user can audit what dropped; their
    target stays the old translation so it's not lost if the user wants to
    revert. To translate, the user feeds the CSV back through `game-translator
    translate` — fallback rows (status=changed/new with empty target) get
    re-translated; unchanged rows are kept as-is.
    """
    def _write(fp: IO[str]) -> None:
        writer = csv.DictWriter(fp, fieldnames=["file", "key", "source", "target", "context", "sub_keys", "status"])
        writer.writeheader()
        for r in report.unchanged:
            writer.writerow({
                "file": str(r.file), "key": r.key, "source": r.source, "target": r.target,
                "context": r.context, "sub_keys": "\x1f".join(r.sub_keys) if r.sub_keys else "",
                "status": "unchanged",
            })
        for entry, old_target in report.changed:
            writer.writerow({
                "file": str(entry.file), "key": entry.key, "source": entry.source,
                "target": "",  # blanked so resume will re-translate
                "context": entry.context, "sub_keys": "",
                "status": f"changed (was: {old_target[:80]})" if old_target else "changed",
            })
        for entry in report.new:
            writer.writerow({
                "file": str(entry.file), "key": entry.key, "source": entry.source,
                "target": "", "context": entry.context, "sub_keys": "",
                "status": "new",
            })
        for r in report.removed:
            writer.writerow({
                "file": str(r.file), "key": r.key, "source": r.source, "target": r.target,
                "context": r.context, "sub_keys": "\x1f".join(r.sub_keys) if r.sub_keys else "",
                "status": "removed",
            })
    _atomic_write_text(out, _write)


def run_diff(old_csv: Path, new_csv: Path, translations_csv: Path | None, out_csv: Path) -> DiffReport:
    old_entries = load_entries(old_csv)
    new_entries = load_entries(new_csv)
    old_translations = load_results(translations_csv) if translations_csv and translations_csv.exists() else []
    report = diff_entries(old_entries, new_entries, old_translations)
    write_diff_report(report, out_csv)
    return report
