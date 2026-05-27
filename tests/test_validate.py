from __future__ import annotations

from game_llm_translator.models import TranslationResult
from game_llm_translator.validate import (
    check_noun_consistency,
    format_noun_warnings,
    is_cjk_leak,
    needs_retry,
    translation_warnings,
)
from pathlib import Path


def test_translation_warnings_detects_missing_placeholder():
    issues = translation_warnings("Hello \\V[1]", "Xin chào")
    assert any("missing placeholder" in i for i in issues)


def test_translation_warnings_empty_when_unchanged():
    assert translation_warnings("Hello", "Hello") == []


def test_translation_warnings_ok_when_placeholder_preserved():
    assert translation_warnings("HP: \\V[1]", "HP: \\V[1]") == []


def test_translation_warnings_context_name_overflow():
    issues = translation_warnings("短剣", "Dagger of the ancient dragon kings", context="rpg_maker_weapons_name")
    assert any("name/UI label too long" in i for i in issues)


def test_translation_warnings_context_name_ok():
    assert translation_warnings("短剣", "Dagger", context="rpg_maker_weapons_name") == []


def test_translation_warnings_context_dialogue_longer_ok():
    assert translation_warnings("これは長い文章です。", "This is a somewhat longer translation of the sentence.", context="rpg_maker_event_text") == []


def test_translation_warnings_no_context_falls_back():
    issues = translation_warnings("短剣", "Dagger of the ancient dragon kings")
    assert not any("name/UI label" in i for i in issues)


def test_noun_consistency_no_inconsistency():
    results = [
        TranslationResult(Path("a.json"), "$[1].name", "勇者", "Dũng giả", "rpg_maker_actors_name"),
        TranslationResult(Path("a.json"), "$[2].name", "ポーション", "Bình thuốc", "rpg_maker_items_name"),
    ]
    assert check_noun_consistency(results) == []


def test_noun_consistency_detects_inconsistency():
    results = [
        TranslationResult(Path("a.json"), "$[1].name", "勇者", "Dũng giả", "rpg_maker_actors_name"),
        TranslationResult(Path("b.json"), "$[5].name", "勇者", "Anh hùng", "rpg_maker_actors_name"),
    ]
    issues = check_noun_consistency(results)
    assert len(issues) == 1
    assert issues[0][0] == "勇者"
    assert "Dũng giả" in issues[0][1]
    assert "Anh hùng" in issues[0][1]


def test_noun_consistency_ignores_non_name_contexts():
    results = [
        TranslationResult(Path("a.json"), "$[1].list[0].parameters[0]", "こんにちは", "Xin chào", "rpg_maker_event_text"),
        TranslationResult(Path("a.json"), "$[2].list[0].parameters[0]", "こんにちは", "Chào bạn", "rpg_maker_event_text"),
    ]
    assert check_noun_consistency(results) == []


def test_noun_consistency_ignores_identical_translations():
    results = [
        TranslationResult(Path("a.json"), "$[1].name", "スライム", "Slime", "rpg_maker_enemies_name"),
        TranslationResult(Path("a.json"), "$[2].name", "スライム", "Slime", "rpg_maker_enemies_name"),
    ]
    assert check_noun_consistency(results) == []


def test_noun_consistency_ignores_fallback():
    results = [
        TranslationResult(Path("a.json"), "$[1].name", "テスト", "テスト", "rpg_maker_actors_name"),
    ]
    assert check_noun_consistency(results) == []


def test_format_noun_warnings():
    issues = [("勇者", ["Dũng giả", "Anh hùng"]), ("魔法", ["Ma thuật", "Phép thuật"])]
    warnings = format_noun_warnings(issues)
    assert len(warnings) == 2
    assert "勇者" in warnings[0]
    assert "Dũng giả" in warnings[0]
    assert "Anh hùng" in warnings[0]


def test_format_noun_warnings_truncates():
    issues = [(f"term{i}", [f"trans{i}a", f"trans{i}b"]) for i in range(50)]
    warnings = format_noun_warnings(issues, max_items=10)
    assert len(warnings) == 11
    assert "40 more" in warnings[-1]


def test_noun_consistency_detects_speaker_name_inconsistency():
    results = [
        TranslationResult(Path("a.json"), "$[1].parameters[4]", "特蕾西亚", "Theresia", "rpg_maker_speaker_name"),
        TranslationResult(Path("b.json"), "$[2].parameters[4]", "特蕾西亚", "Teresia", "rpg_maker_speaker_name"),
    ]
    issues = check_noun_consistency(results)
    assert len(issues) == 1
    assert issues[0][0] == "特蕾西亚"


def test_translation_warnings_speaker_name_overflow():
    issues = translation_warnings("ア", "A very long translated speaker name that overflows", context="rpg_maker_speaker_name")
    assert any("name/UI label too long" in i for i in issues)


def test_translation_warnings_state_description_ok():
    issues = translation_warnings("毒状態のキャラクター", "Poisoned character takes damage", context="rpg_maker_states_description")
    assert issues == []


def test_translation_warnings_state_description_too_long():
    issues = translation_warnings("毒", "A very long description that is way too much for a short source", context="rpg_maker_states_description")
    assert any("description too long" in i for i in issues)


def test_translation_warnings_system_title_overflow():
    issues = translation_warnings("短い", "An extremely long game title that would overflow the title screen", context="rpg_maker_system_gameTitle")
    assert any("name/UI label too long" in i for i in issues)


def test_is_cjk_leak_detects_kanji():
    assert is_cjk_leak("Dũng giả 勇者 đến rồi")


def test_is_cjk_leak_detects_hiragana():
    assert is_cjk_leak("Xin chào こんにちは")


def test_is_cjk_leak_detects_katakana():
    assert is_cjk_leak("Bình ポーション")


def test_is_cjk_leak_detects_hangul():
    assert is_cjk_leak("안녕 hello")


def test_is_cjk_leak_clean_vietnamese():
    assert not is_cjk_leak("Xin chào, đây là bản dịch sạch")


def test_is_cjk_leak_empty():
    assert not is_cjk_leak("")


def test_needs_retry_empty_target():
    assert needs_retry("こんにちは", "")
    assert needs_retry("こんにちは", "   ")


def test_needs_retry_fallback():
    assert needs_retry("Hello", "Hello")


def test_needs_retry_cjk_leak():
    assert needs_retry("こんにちは", "Xin chào こんにちは")


def test_needs_retry_clean_translation():
    assert not needs_retry("こんにちは", "Xin chào")


def test_translation_warnings_flags_cjk_leak():
    issues = translation_warnings("勇者が来た", "Dũng giả 勇者 đến", context="rpg_maker_event_text")
    assert any("CJK characters" in i for i in issues)


def test_translation_warnings_clean_no_cjk_warning():
    issues = translation_warnings("勇者が来た", "Dũng giả đến", context="rpg_maker_event_text")
    assert not any("CJK characters" in i for i in issues)
