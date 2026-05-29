from __future__ import annotations

from pathlib import Path

from game_llm_translator.models import TranslationResult
from game_llm_translator.validate import (
    check_noun_consistency,
    format_noun_warnings,
    is_cjk_leak,
    needs_retry,
    translation_warnings,
)


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


def test_vxace_validation_preserves_control_codes():
    warnings = translation_warnings("寄存\\C[23]\\N[7]", "Cất giữ", "rpg_maker_map_choice")
    assert any("missing control code" in w for w in warnings)


def test_vxace_validation_preserves_angle_tags():
    warnings = translation_warnings("<战斗结束时退队>", "Rời đội khi kết thúc chiến đấu", "rpg_maker_vxace_note")
    assert any("missing tag" in w for w in warnings)


def test_vxace_script_string_rejects_unescaped_newline():
    warnings = translation_warnings("精神值变化:", "Dòng 1\nDòng 2", "rpg_maker_map_script_string")
    assert any("script string contains newline" in w for w in warnings)


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
