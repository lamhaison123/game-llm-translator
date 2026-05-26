from __future__ import annotations

from pathlib import Path

from game_llm_translator.csv_store import save_entries, save_results
from game_llm_translator.diff_tool import diff_entries, run_diff
from game_llm_translator.models import TextEntry, TranslationResult


def _entry(file: str, key: str, source: str) -> TextEntry:
    return TextEntry(Path(file), key, source, "ctx", "")


def _result(file: str, key: str, source: str, target: str) -> TranslationResult:
    return TranslationResult(Path(file), key, source, target, "ctx")


def test_diff_entries_unchanged_carries_translation():
    old = [_entry("a.json", "k1", "hello")]
    new = [_entry("a.json", "k1", "hello")]
    translations = [_result("a.json", "k1", "hello", "xin chào")]

    report = diff_entries(old, new, translations)

    assert len(report.unchanged) == 1
    assert report.unchanged[0].target == "xin chào"
    assert report.changed == []
    assert report.new == []
    assert report.removed == []


def test_diff_entries_changed_blanks_target():
    old = [_entry("a.json", "k1", "hello v1")]
    new = [_entry("a.json", "k1", "hello v2")]
    translations = [_result("a.json", "k1", "hello v1", "xin chào v1")]

    report = diff_entries(old, new, translations)

    assert len(report.changed) == 1
    new_entry, old_target = report.changed[0]
    assert new_entry.source == "hello v2"
    assert old_target == "xin chào v1"  # context for the user
    assert report.unchanged == []


def test_diff_entries_new_entries():
    old = [_entry("a.json", "k1", "hello")]
    new = [_entry("a.json", "k1", "hello"), _entry("a.json", "k2", "world")]
    translations = [_result("a.json", "k1", "hello", "xin chào")]

    report = diff_entries(old, new, translations)

    assert len(report.new) == 1
    assert report.new[0].key == "k2"
    assert len(report.unchanged) == 1


def test_diff_entries_removed_kept_for_audit():
    old = [_entry("a.json", "k1", "hello"), _entry("a.json", "k2", "world")]
    new = [_entry("a.json", "k1", "hello")]
    translations = [
        _result("a.json", "k1", "hello", "xin chào"),
        _result("a.json", "k2", "world", "thế giới"),
    ]

    report = diff_entries(old, new, translations)

    assert len(report.removed) == 1
    assert report.removed[0].key == "k2"
    assert report.removed[0].target == "thế giới"  # not lost


def test_diff_entries_unchanged_without_prior_translation_becomes_new():
    """If source is unchanged but we have no prior target, treat as new."""
    old = [_entry("a.json", "k1", "hello")]
    new = [_entry("a.json", "k1", "hello")]
    translations: list = []  # never translated

    report = diff_entries(old, new, translations)

    assert report.unchanged == []
    assert len(report.new) == 1


def test_diff_entries_empty_target_treated_as_no_translation():
    """A fallback row (target == "") should NOT carry forward as unchanged."""
    old = [_entry("a.json", "k1", "hello")]
    new = [_entry("a.json", "k1", "hello")]
    translations = [_result("a.json", "k1", "hello", "")]  # never filled in

    report = diff_entries(old, new, translations)

    assert report.unchanged == []
    assert len(report.new) == 1


def test_run_diff_writes_status_column(tmp_path):
    old_csv = tmp_path / "old.csv"
    new_csv = tmp_path / "new.csv"
    tr_csv = tmp_path / "translations.csv"
    out_csv = tmp_path / "diff.csv"

    save_entries([
        _entry("a.json", "k1", "hello"),
        _entry("a.json", "k2", "old line"),
        _entry("a.json", "k3", "removed"),
    ], old_csv)
    save_entries([
        _entry("a.json", "k1", "hello"),
        _entry("a.json", "k2", "new line"),
        _entry("a.json", "k4", "brand new"),
    ], new_csv)
    save_results([
        _result("a.json", "k1", "hello", "xin chào"),
        _result("a.json", "k2", "old line", "dòng cũ"),
        _result("a.json", "k3", "removed", "đã xóa"),
    ], tr_csv)

    report = run_diff(old_csv, new_csv, tr_csv, out_csv)

    assert report.stats == {"unchanged": 1, "changed": 1, "new": 1, "removed": 1}
    content = out_csv.read_text(encoding="utf-8")
    assert "unchanged" in content
    assert "changed" in content
    assert "new" in content
    assert "removed" in content
    assert "xin chào" in content  # carried forward
