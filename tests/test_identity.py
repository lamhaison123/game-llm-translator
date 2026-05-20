from __future__ import annotations

from pathlib import Path

from game_llm_translator.models import TranslationResult, text_identity
from game_llm_translator.translate_pipeline import dedupe_results


def test_dedupe_results_keeps_same_key_in_different_files():
    actors = TranslationResult(Path("Actors.json"), "$[1].name", "Harold", "Ha-rôn")
    items = TranslationResult(Path("Items.json"), "$[1].name", "Potion", "Thuốc")
    wanted = {text_identity(actors.file, actors.key), text_identity(items.file, items.key)}

    assert dedupe_results([actors, items], wanted) == [actors, items]


def test_dedupe_results_last_wins_for_same_file_and_key():
    first = TranslationResult(Path("Actors.json"), "$[1].name", "Harold", "A")
    second = TranslationResult(Path("Actors.json"), "$[1].name", "Harold", "B")
    wanted = {text_identity(first.file, first.key)}

    assert dedupe_results([first, second], wanted) == [second]
