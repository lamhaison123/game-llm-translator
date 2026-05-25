from __future__ import annotations

from pathlib import Path

import pytest

from game_llm_translator.glossary import build_auto_glossary, format_glossary_categorized, format_glossary_for_prompt, load_glossary, load_glossary_with_categories, speaker_name_glossary_from_results


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


def test_load_glossary_with_categories(tmp_path):
    path = tmp_path / "glossary.csv"
    path.write_text(
        "term,translation,note,category\n勇者,Dũng giả,protagonist,character\nポーション,Bình thuốc,common,item\n魔法,Ma thuật,,skill\n",
        encoding="utf-8",
    )
    entries = load_glossary_with_categories(path)
    assert entries == [
        ("勇者", "Dũng giả", "character"),
        ("ポーション", "Bình thuốc", "item"),
        ("魔法", "Ma thuật", "skill"),
    ]


def test_load_glossary_with_categories_no_category_column(tmp_path):
    path = tmp_path / "glossary.csv"
    path.write_text("term,translation\nfoo,Bar\n", encoding="utf-8")
    entries = load_glossary_with_categories(path)
    assert entries == [("foo", "Bar", "")]


def test_load_glossary_with_categories_missing_file(tmp_path):
    assert load_glossary_with_categories(tmp_path / "nope.csv") == []


def test_load_glossary_with_categories_none():
    assert load_glossary_with_categories(None) == []


def test_format_glossary_categorized_no_categories():
    entries = [("foo", "Bar", ""), ("baz", "Qux", "")]
    text = format_glossary_categorized(entries)
    assert "## Glossary" in text
    assert "###" not in text
    assert '"foo" -> "Bar"' in text


def test_format_glossary_categorized_with_categories():
    entries = [
        ("foo", "Bar", ""),
        ("勇者", "Dũng giả", "character"),
        ("HP回復", "Hồi HP", "skill"),
        ("ポーション", "Bình thuốc", "item"),
    ]
    text = format_glossary_categorized(entries)
    assert "## Glossary" in text
    assert "### character" in text
    assert "### item" in text
    assert "### skill" in text
    assert '"foo" -> "Bar"' in text
    assert '"勇者" -> "Dũng giả"' in text


def test_format_glossary_categorized_empty():
    assert format_glossary_categorized([]) == ""


def test_format_glossary_categorized_truncation():
    entries = [(f"term{i}", f"trans{i}", "cat") for i in range(200)]
    text = format_glossary_categorized(entries, max_chars=200)
    assert "more terms omitted" in text


# ---------------------------------------------------------------------------
# build_auto_glossary
# ---------------------------------------------------------------------------

def test_build_auto_glossary_extracts_name_contexts():
    results = [
        ("Harold", "Ha-rôn", "rpg_maker_actors_name"),
        ("Attack", "Tấn công", "rpg_maker_skills_name"),
        ("Potion", "Thuốc", "rpg_maker_items_name"),
        ("Town Entrance", "Lối vào thị trấn", "rpg_maker_map_display_name"),
    ]
    glossary = build_auto_glossary(results)
    assert len(glossary) == 4
    assert ("Harold", "Ha-rôn") in glossary
    assert ("Attack", "Tấn công") in glossary


def test_build_auto_glossary_skips_non_name_contexts():
    results = [
        ("A brave warrior.", "Một chiến binh dũng cảm.", "rpg_maker_actors_profile"),
        ("Deals damage", "Gây sát thương", "rpg_maker_skills_description"),
    ]
    glossary = build_auto_glossary(results)
    assert len(glossary) == 0


def test_build_auto_glossary_skips_empty_and_same():
    results = [
        ("", "", "rpg_maker_actors_name"),
        ("HP", "HP", "rpg_maker_system_elements"),
        ("Harold", "", "rpg_maker_actors_name"),
    ]
    glossary = build_auto_glossary(results)
    assert len(glossary) == 0


def test_build_auto_glossary_deduplicates():
    results = [
        ("Attack", "Tấn công", "rpg_maker_skills_name"),
        ("Attack", "Công kích", "rpg_maker_items_name"),
    ]
    glossary = build_auto_glossary(results)
    assert len(glossary) == 1
    assert glossary[0] == ("Attack", "Tấn công")




def test_speaker_name_glossary_prefers_namebox_aliases():
    results = [
        ("白橋希", "Bạch Kiều Hi", "rpg_maker_actors_name"),
        ("測試員", "Kiểm thử viên", "rpg_maker_actors_name"),
        ("\\n<希>「これは……」", "\\n<Hi>「Đây là……」", "rpg_maker_event_text"),
    ]

    glossary = speaker_name_glossary_from_results(results)

    assert glossary == [("希", "Bạch Kiều Hi"), ("測試員", "Kiểm thử viên")]


def test_speaker_name_glossary_prefers_database_name_translation():
    results = [
        ("白橋希", "Bạch Kiều Hi", "rpg_maker_actors_name"),
        ("\\n<希>「これは……」", "\\n<Hi>「Đây là……」", "rpg_maker_event_text"),
    ]

    glossary = speaker_name_glossary_from_results(results)

    assert ("希", "Bạch Kiều Hi") in glossary


def test_speaker_name_glossary_ignores_untranslated_database_names():
    results = [
        ("白橋希", "白橋希", "rpg_maker_actors_name"),
        ("\\n<希>「これは……」", "\\n<Hi>「Đây là……」", "rpg_maker_event_text"),
    ]

    glossary = speaker_name_glossary_from_results(results)

    assert ("希", "Bạch Kiều Hi") not in glossary


def test_build_auto_glossary_respects_max_entries():
    results = [(f"Name{i}", f"Trans{i}", "rpg_maker_actors_name") for i in range(100)]
    glossary = build_auto_glossary(results, max_entries=10)
    assert len(glossary) == 10
