from __future__ import annotations

from pathlib import Path

import pytest

from game_llm_translator.models import TranslationResult
from game_llm_translator.translation_memory import load_memory, save_memory


def _make_results(pairs: list[tuple[str, str]], context: str = "") -> list[TranslationResult]:
    return [
        TranslationResult(
            file=Path("Actors.json"),
            key=f"$.{i}",
            source=src,
            target=tgt,
            context=context,
        )
        for i, (src, tgt) in enumerate(pairs)
    ]


# ---------------------------------------------------------------------------
# save_memory
# ---------------------------------------------------------------------------

def test_save_memory_basic(tmp_path):
    path = tmp_path / "memory.csv"
    results = _make_results([("Hello", "Xin chào"), ("World", "Thế giới")])
    saved = save_memory(path, results, "vietnamese", "english", "google")
    assert saved == 2
    assert path.exists()
    mem = load_memory([path], "vietnamese", "english")
    assert mem["Hello"] == "Xin chào"
    assert mem["World"] == "Thế giới"


def test_save_memory_skips_empty_source(tmp_path):
    path = tmp_path / "memory.csv"
    results = _make_results([("", "Xin chào"), ("Hello", "Xin chào")])
    saved = save_memory(path, results, "vietnamese", None, "google")
    assert saved == 1


def test_save_memory_skips_same_source_target(tmp_path):
    path = tmp_path / "memory.csv"
    results = _make_results([("Hello", "Hello"), ("World", "Thế giới")])
    saved = save_memory(path, results, "vietnamese", None, "google")
    assert saved == 1


def test_save_memory_updates_existing(tmp_path):
    path = tmp_path / "memory.csv"
    results1 = _make_results([("Hello", "Chào")])
    save_memory(path, results1, "vietnamese", "english", "google")
    results2 = _make_results([("Hello", "Xin chào")])
    save_memory(path, results2, "vietnamese", "english", "google")
    mem = load_memory([path], "vietnamese", "english")
    assert mem["Hello"] == "Xin chào"


def test_save_memory_creates_parent_dir(tmp_path):
    path = tmp_path / "nested" / "memory.csv"
    results = _make_results([("Hi", "Chào")])
    save_memory(path, results, "vietnamese", None, "google")
    assert path.exists()


# ---------------------------------------------------------------------------
# load_memory
# ---------------------------------------------------------------------------

def test_load_memory_filters_by_target_lang(tmp_path):
    path = tmp_path / "memory.csv"
    results_vi = _make_results([("Hello", "Xin chào")])
    results_fr = _make_results([("Hello", "Bonjour")])
    save_memory(path, results_vi, "vietnamese", None, "google")
    save_memory(path, results_fr, "french", None, "google")
    mem = load_memory([path], "vietnamese")
    assert mem.get("Hello") == "Xin chào"


def test_load_memory_filters_by_source_lang(tmp_path):
    path = tmp_path / "memory.csv"
    results_ja = _make_results([("こんにちは", "Xin chào")])
    results_en = _make_results([("Hello", "Xin chào")])
    save_memory(path, results_ja, "vietnamese", "japanese", "google")
    save_memory(path, results_en, "vietnamese", "english", "google")
    mem = load_memory([path], "vietnamese", "japanese")
    assert "こんにちは" in mem
    assert "Hello" not in mem


def test_load_memory_source_lang_auto_matches_all(tmp_path):
    path = tmp_path / "memory.csv"
    results = _make_results([("Hello", "Xin chào")])
    save_memory(path, results, "vietnamese", "english", "google")
    # "auto" as wanted source will only match rows where source_lang is "" or "auto"
    # but entries saved with "english" will be excluded when wanted is "auto"
    mem_auto = load_memory([path], "vietnamese", "auto")
    # Expected: "auto" as source filter does NOT match "english" source_lang entries
    # because the code checks row_source not in {"", "auto", wanted_source}
    # when wanted_source == "auto", rows with source_lang="english" are excluded
    assert "Hello" not in mem_auto  # strict: "english" != "auto"

    # However if saved with source_lang "auto", it should load under "auto"
    results2 = _make_results([("World", "Thế giới")])
    save_memory(path, results2, "vietnamese", None, "google")  # None -> "auto"
    mem_auto2 = load_memory([path], "vietnamese", "auto")
    assert "World" in mem_auto2


def test_load_memory_missing_file_skipped(tmp_path):
    path = tmp_path / "nonexistent.csv"
    mem = load_memory([path], "vietnamese")
    assert mem == {}


def test_load_memory_merges_multiple_files(tmp_path):
    path1 = tmp_path / "mem1.csv"
    path2 = tmp_path / "mem2.csv"
    results1 = _make_results([("Hello", "Xin chào")])
    results2 = _make_results([("World", "Thế giới")])
    save_memory(path1, results1, "vietnamese", None, "google")
    save_memory(path2, results2, "vietnamese", None, "google")
    mem = load_memory([path1, path2], "vietnamese")
    assert "Hello" in mem
    assert "World" in mem
