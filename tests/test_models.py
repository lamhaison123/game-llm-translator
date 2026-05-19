from __future__ import annotations

from pathlib import Path

from game_llm_translator.models import TextEntry, TranslationResult


def test_text_entry_defaults():
    entry = TextEntry(file=Path("test.json"), key="$[0].name", source="Hello")
    assert entry.file == Path("test.json")
    assert entry.key == "$[0].name"
    assert entry.source == "Hello"
    assert entry.context == ""
    assert entry.context_text == ""


def test_text_entry_with_context():
    entry = TextEntry(
        file=Path("Map001.json"),
        key="$[1].name",
        source="World",
        context="rpg_maker_json",
        context_text="Some context",
    )
    assert entry.context == "rpg_maker_json"
    assert entry.context_text == "Some context"


def test_translation_result_defaults():
    result = TranslationResult(
        file=Path("Actors.json"),
        key="$[0].name",
        source="Hero",
        target="Anh hùng",
    )
    assert result.file == Path("Actors.json")
    assert result.key == "$[0].name"
    assert result.source == "Hero"
    assert result.target == "Anh hùng"
    assert result.context == ""


def test_translation_result_with_context():
    result = TranslationResult(
        file=Path("Actors.json"),
        key="$[0].name",
        source="Hero",
        target="Anh hùng",
        context="rpg_maker_json",
    )
    assert result.context == "rpg_maker_json"
