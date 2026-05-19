from __future__ import annotations

from pathlib import Path

from game_llm_translator.auto import _dedupe_results as auto_dedupe
from game_llm_translator.cli import _dedupe_results as cli_dedupe
from game_llm_translator.models import TranslationResult, text_identity


def test_dedupe_results_keeps_same_key_in_different_files():
    actors = TranslationResult(Path("Actors.json"), "$[1].name", "Harold", "Ha-rôn")
    items = TranslationResult(Path("Items.json"), "$[1].name", "Potion", "Thuốc")
    wanted = {text_identity(actors.file, actors.key), text_identity(items.file, items.key)}

    assert cli_dedupe([actors, items], wanted) == [actors, items]
    assert auto_dedupe([actors, items], wanted) == [actors, items]


def test_dedupe_results_last_wins_for_same_file_and_key():
    first = TranslationResult(Path("Actors.json"), "$[1].name", "Harold", "A")
    second = TranslationResult(Path("Actors.json"), "$[1].name", "Harold", "B")
    wanted = {text_identity(first.file, first.key)}

    assert cli_dedupe([first, second], wanted) == [second]
    assert auto_dedupe([first, second], wanted) == [second]
