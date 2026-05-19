from __future__ import annotations

from pathlib import Path

import pytest

from game_llm_translator.csv_store import (
    load_entries,
    load_results,
    save_entries,
    save_results,
)
from game_llm_translator.models import TextEntry, TranslationResult


# ---------------------------------------------------------------------------
# save_entries / load_entries
# ---------------------------------------------------------------------------

def test_save_and_load_entries_roundtrip(tmp_path):
    entries = [
        TextEntry(Path("Actors.json"), "$[1].name", "Harold", "rpg_maker_json", "context text"),
        TextEntry(Path("Map001.json"), "$[1].events[1].pages[0].list[0].parameters[0]", "Hello!", "rpg_maker_event_text", ""),
    ]
    csv_file = tmp_path / "texts.csv"
    save_entries(entries, csv_file)
    loaded = load_entries(csv_file)
    assert len(loaded) == 2
    assert loaded[0].key == "$[1].name"
    assert loaded[0].source == "Harold"
    assert loaded[0].context == "rpg_maker_json"
    assert loaded[0].context_text == "context text"
    assert loaded[1].source == "Hello!"


def test_save_entries_creates_parent_dir(tmp_path):
    entries = [TextEntry(Path("Actors.json"), "$.name", "Test")]
    csv_file = tmp_path / "deep" / "texts.csv"
    save_entries(entries, csv_file)
    assert csv_file.exists()


def test_load_entries_missing_optional_fields(tmp_path):
    """CSV without context/context_text columns should still load."""
    csv_file = tmp_path / "texts.csv"
    csv_file.write_text("file,key,source\nActors.json,$.name,Harold\n", encoding="utf-8")
    entries = load_entries(csv_file)
    assert entries[0].source == "Harold"
    assert entries[0].context == ""
    assert entries[0].context_text == ""


def test_save_entries_empty(tmp_path):
    csv_file = tmp_path / "texts.csv"
    save_entries([], csv_file)
    loaded = load_entries(csv_file)
    assert loaded == []


# ---------------------------------------------------------------------------
# save_results / load_results
# ---------------------------------------------------------------------------

def test_save_and_load_results_roundtrip(tmp_path):
    results = [
        TranslationResult(Path("Actors.json"), "$[1].name", "Harold", "Ha-rôn", "rpg_maker_json"),
        TranslationResult(Path("Skills.json"), "$[1].name", "Attack", "Tấn công", ""),
    ]
    csv_file = tmp_path / "translations.csv"
    save_results(results, csv_file)
    loaded = load_results(csv_file)
    assert len(loaded) == 2
    assert loaded[0].source == "Harold"
    assert loaded[0].target == "Ha-rôn"
    assert loaded[0].context == "rpg_maker_json"
    assert loaded[1].target == "Tấn công"


def test_save_results_creates_parent_dir(tmp_path):
    results = [TranslationResult(Path("Actors.json"), "$.name", "Harold", "Ha-rôn")]
    csv_file = tmp_path / "nested" / "translations.csv"
    save_results(results, csv_file)
    assert csv_file.exists()


def test_load_results_missing_context_column(tmp_path):
    csv_file = tmp_path / "translations.csv"
    csv_file.write_text(
        "file,key,source,target\nActors.json,$.name,Harold,Ha-rôn\n", encoding="utf-8"
    )
    results = load_results(csv_file)
    assert results[0].target == "Ha-rôn"
    assert results[0].context == ""


def test_save_results_empty(tmp_path):
    csv_file = tmp_path / "translations.csv"
    save_results([], csv_file)
    loaded = load_results(csv_file)
    assert loaded == []


def test_save_results_preserves_unicode(tmp_path):
    results = [
        TranslationResult(Path("x.json"), "$.k", "日本語", "Tiếng Nhật"),
    ]
    csv_file = tmp_path / "translations.csv"
    save_results(results, csv_file)
    loaded = load_results(csv_file)
    assert loaded[0].source == "日本語"
    assert loaded[0].target == "Tiếng Nhật"
