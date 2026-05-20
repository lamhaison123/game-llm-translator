from __future__ import annotations

from game_llm_translator.validate import translation_warnings


def test_translation_warnings_detects_missing_placeholder():
    issues = translation_warnings("Hello \\V[1]", "Xin chào")
    assert any("missing placeholder" in i for i in issues)


def test_translation_warnings_empty_when_unchanged():
    assert translation_warnings("Hello", "Hello") == []


def test_translation_warnings_ok_when_placeholder_preserved():
    assert translation_warnings("HP: \\V[1]", "HP: \\V[1]") == []
