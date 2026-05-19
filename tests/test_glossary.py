from __future__ import annotations

from pathlib import Path

import pytest

from game_llm_translator.glossary import format_glossary_for_prompt, load_glossary


def test_load_glossary_basic(tmp_path):
    path = tmp_path / "glossary.csv"
    path.write_text(
        "term,translation,note\n勇者,Dũng giả,protagonist\nポーション,Bình thuốc,common\n",
        encoding="utf-8",
    )
    entries = load_glossary(path)
    assert entries == [("勇者", "Dũng giả"), ("ポーション", "Bình thuốc")]


def test_load_glossary_skips_missing_term_or_translation(tmp_path):
    path = tmp_path / "glossary.csv"
    path.write_text(
        "term,translation\nfoo,Phú\n,EmptyTerm\nNoTrans,\nbar,Bá\n",
        encoding="utf-8",
    )
    entries = load_glossary(path)
    assert entries == [("foo", "Phú"), ("bar", "Bá")]


def test_load_glossary_dedupes_repeated_term(tmp_path):
    path = tmp_path / "glossary.csv"
    path.write_text("term,translation\nfoo,A\nfoo,B\n", encoding="utf-8")
    entries = load_glossary(path)
    assert entries == [("foo", "A")]


def test_load_glossary_missing_file_returns_empty(tmp_path):
    assert load_glossary(tmp_path / "nope.csv") == []


def test_load_glossary_none_returns_empty():
    assert load_glossary(None) == []


def test_load_glossary_handles_bom(tmp_path):
    path = tmp_path / "glossary.csv"
    path.write_bytes("﻿term,translation\nfoo,Phú\n".encode("utf-8"))
    assert load_glossary(path) == [("foo", "Phú")]


def test_format_glossary_empty_returns_empty_string():
    assert format_glossary_for_prompt([]) == ""


def test_format_glossary_renders_block():
    text = format_glossary_for_prompt([("foo", "Phú"), ("bar", "Bá")])
    assert "## Glossary" in text
    assert '"foo" -> "Phú"' in text
    assert '"bar" -> "Bá"' in text


def test_format_glossary_truncates_at_max_chars():
    entries = [(f"term{i}", f"trans{i}") for i in range(200)]
    text = format_glossary_for_prompt(entries, max_chars=200)
    assert len(text) <= 280  # header + lines + truncation note
    assert "more terms omitted" in text
