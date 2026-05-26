from __future__ import annotations

import json

from types import SimpleNamespace
from pathlib import Path

import pytest

from game_llm_translator.llm import (
    _lang_code,
    _mask_protected_tokens,
    _parse_translation_json,
    _parse_google_translate_response,
    _chat_completion_text,
    _anthropic_message_text,
    _repair_invalid_escapes,
    _restore_protected_tokens,
    _restore_namebox_prefix,
    _results_from_json,
    _user_prompt,
    _fix_token_formatting,
    _build_system_prompt,
    extract_namebox_names,
    _replace_untranslated_namebox_names,
    _parse_name_json,
)
from game_llm_translator.models import TextEntry


# ---------------------------------------------------------------------------
# _lang_code
# ---------------------------------------------------------------------------

def test_lang_code_known_names():
    assert _lang_code("Vietnamese") == "vi"
    assert _lang_code("japanese") == "ja"
    assert _lang_code("ENGLISH") == "en"
    assert _lang_code("korean") == "ko"
    assert _lang_code("chinese") == "zh"
    assert _lang_code("french") == "fr"
    assert _lang_code("german") == "de"
    assert _lang_code("spanish") == "es"
    assert _lang_code("russian") == "ru"


def test_lang_code_known_codes():
    assert _lang_code("vi") == "vi"
    assert _lang_code("ja") == "ja"
    assert _lang_code("en") == "en"


def test_lang_code_auto():
    assert _lang_code("auto") == "auto"


def test_lang_code_none_returns_default():
    assert _lang_code(None) == "auto"
    assert _lang_code(None, default="vi") == "vi"


def test_lang_code_unknown_raises():
    with pytest.raises(ValueError, match="Unsupported language"):
        _lang_code("unknown_lang")


def test_lang_code_empty_string_returns_default():
    assert _lang_code("") == "auto"


# ---------------------------------------------------------------------------
# _mask_protected_tokens / _restore_protected_tokens
# ---------------------------------------------------------------------------

def test_mask_restore_variable():
    text = "Hello \\V[1], welcome!"
    masked, mapping = _mask_protected_tokens(text)
    assert "\\V[1]" not in masked
    assert len(mapping) == 1
    restored = _restore_protected_tokens(masked, mapping)
    assert restored == text


def test_mask_restore_multiple_tokens():
    text = "\\N[1] scored %1 points in <area> with \\C[3]!"
    masked, mapping = _mask_protected_tokens(text)
    # Each token should be replaced
    assert "\\N[1]" not in masked
    assert "%1" not in masked
    assert "<area>" not in masked
    assert "\\C[3]" not in masked
    restored = _restore_protected_tokens(masked, mapping)
    assert restored == text


def test_mask_restore_no_tokens():
    text = "Simple text without any special tokens."
    masked, mapping = _mask_protected_tokens(text)
    assert masked == text
    assert mapping == {}
    restored = _restore_protected_tokens(masked, mapping)
    assert restored == text


def test_mask_restore_curly_brace_placeholder():
    text = "Welcome, {player_name}!"
    masked, mapping = _mask_protected_tokens(text)
    assert "{player_name}" not in masked
    restored = _restore_protected_tokens(masked, mapping)
    assert restored == text


def test_mask_restore_percent_format():
    text = "Score: %s of %d."
    masked, mapping = _mask_protected_tokens(text)
    restored = _restore_protected_tokens(masked, mapping)
    assert restored == text


def test_mask_restore_party_member_token():
    text = "\\P[1] attacks!"
    masked, mapping = _mask_protected_tokens(text)
    restored = _restore_protected_tokens(masked, mapping)
    assert restored == text


def test_mask_restore_underscore_token():
    text = "Hello\\_World"
    masked, mapping = _mask_protected_tokens(text)
    restored = _restore_protected_tokens(masked, mapping)
    assert restored == text


def test_restore_case_insensitive_token():
    """If LLM lowercases the placeholder token, it should still be restored."""
    text = "\\V[1]"
    masked, mapping = _mask_protected_tokens(text)
    lowercased = masked.lower()
    restored = _restore_protected_tokens(lowercased, mapping)
    assert "\\V[1]" in restored


def test_restore_does_not_false_positive_lowercase():
    """Lowercase version of token should NOT match unrelated text."""
    text = "\\N[1]"
    masked, mapping = _mask_protected_tokens(text)
    assert len(mapping) == 1
    token = list(mapping.keys())[0]
    fake = "the word " + token.lower() + " should not be replaced"
    restored = _restore_protected_tokens(fake, mapping)
    assert "the word" in restored
    assert restored != fake or token.lower() == token


def test_mask_restore_mz_plugin_tokens():
    text = "\\F[smile] says \\FFF[happy] with \\FH[ON] highlight"
    masked, mapping = _mask_protected_tokens(text)
    restored = _restore_protected_tokens(masked, mapping)
    assert restored == text


def test_mask_restore_mz_outline_tokens():
    text = "\\OC[3] text \\OO[5] more \\FS[24]"
    masked, mapping = _mask_protected_tokens(text)
    restored = _restore_protected_tokens(masked, mapping)
    assert restored == text


def test_mask_namebox_name_visible():
    """Speaker name inside <...> must be visible to LLM after masking."""
    text = "\\n<\\C[22]フォル>「バカな……」"
    masked, mapping = _mask_protected_tokens(text)
    assert "フォル" in masked, f"Name should be visible, got: {masked!r}"
    assert "\\C[22]" not in masked, f"Color code should be masked, got: {masked!r}"
    assert "\\n" not in masked, f"\\\\n should be masked, got: {masked!r}"
    assert "<" in masked and ">" in masked, f"Delimiters should be visible, got: {masked!r}"


def test_mask_namebox_simple():
    """Simple \\n<Name> namebox — name visible, \\n masked."""
    text = "\\n<希>こんにちは"
    masked, mapping = _mask_protected_tokens(text)
    assert "希" in masked
    assert "\\n" not in masked
    restored = _restore_protected_tokens(masked, mapping)
    assert restored == text


def test_mask_namebox_with_face():
    """Face + namebox \\F[N_01]\\n<希> — name visible, control codes masked."""
    text = "\\F[N_01]\\n<希>待って！"
    masked, mapping = _mask_protected_tokens(text)
    assert "希" in masked
    assert "\\F[N_01]" not in masked
    assert "\\n" not in masked
    restored = _restore_protected_tokens(masked, mapping)
    assert restored == text


def test_mask_non_namebox_angle_brackets():
    """Non-namebox tags like <area> must still be fully masked."""
    text = "Score: <area> and %1 damage"
    masked, mapping = _mask_protected_tokens(text)
    assert "area" not in masked, f"<area> should be fully masked, got: {masked!r}"
    assert "<area>" not in masked
    restored = _restore_protected_tokens(masked, mapping)
    assert restored == text


def test_mask_namebox_roundtrip():
    """Mask + restore preserves original text for namebox content."""
    text = "\\n<\\C[22]フォル>「バカな……この氷は……」"
    masked, mapping = _mask_protected_tokens(text)
    restored = _restore_protected_tokens(masked, mapping)
    assert restored == text


def test_restore_namebox_preserves_translated_name():
    """If LLM translates the name inside <...>, keep the translated name."""
    source = "\\n<\\C[22]フォル>「バカな……」"
    target = "\\n<\\C[22]Foru>\"Không thể nào...\""
    result = _restore_namebox_prefix(source, target)
    assert "Foru" in result, f"Translated name should be kept, got: {result!r}"
    assert "\\C[22]" in result, f"Color code should be preserved, got: {result!r}"


def test_restore_namebox_restores_dropped_prefix():
    """If LLM drops the namebox entirely, restore the source prefix."""
    source = "\\n<\\C[22]フォル>「バカな……」"
    target = "\"Không thể nào...\""
    result = _restore_namebox_prefix(source, target)
    assert result.startswith("\\n<\\C[22]フォル>"), f"Prefix should be restored, got: {result!r}"


def test_restore_namebox_restores_dropped_color_code():
    """If LLM translates the name but drops color codes inside <...>, restore them."""
    source = "\\n<\\C[22]フォル>「バカな……」"
    target = "\\n<Foru>\"Không thể nào...\""
    result = _restore_namebox_prefix(source, target)
    assert "Foru" in result, f"Translated name should be kept, got: {result!r}"
    assert "\\C[22]" in result, f"Color code should be restored, got: {result!r}"


def test_restore_namebox_same_name_no_change():
    """If LLM keeps the namebox with same name, no change needed."""
    source = "\\n<\\C[22]フォル>「バカな……」"
    target = "\\n<\\C[22]フォル>\"Không thể nào...\""
    result = _restore_namebox_prefix(source, target)
    assert result == target


def test_namebox_warning_when_speaker_name_left_untranslated():
    from game_llm_translator.validate import translation_warnings

    source = "\\FF[greima_13]\\n<\\C[27]グレーマ>「それじゃあ……また明日会いましょう……」"
    target = "\\FF[greima_13]\\n<\\C[27]グレーマ>「Vậy thì… ngày mai gặp lại nhé……」"

    assert "namebox speaker name was not translated" in translation_warnings(source, target, "rpg_maker_event_text")


def test_system_prompt_requires_translating_namebox_speaker_names():
    prompt = _build_system_prompt("Vietnamese")

    assert "MUST translate or transliterate the speaker name inside <...>" in prompt
    assert "<\\C[27]グレーマ>" in prompt
    assert "<\\C[27]Gurema>" in prompt


# ---------------------------------------------------------------------------
# _parse_translation_json
# ---------------------------------------------------------------------------

def test_parse_translation_json_basic():
    data = [{"key": "a", "target": "b"}]
    result, stats = _parse_translation_json(json.dumps(data))
    assert result == data
    assert stats.parsed == 1


def test_parse_translation_json_strips_code_fence():
    raw = "```json\n[{\"key\": \"a\", \"target\": \"b\"}]\n```"
    result, _ = _parse_translation_json(raw)
    assert result[0]["target"] == "b"


def test_parse_translation_json_strips_plain_fence():
    raw = "```\n[{\"key\": \"a\", \"target\": \"b\"}]\n```"
    result, _ = _parse_translation_json(raw)
    assert result[0]["target"] == "b"


def test_parse_translation_json_filters_incomplete():
    data = [
        {"key": "a", "target": "b"},
        {"key": "c"},          # missing "target"
        {"target": "d"},       # missing "key"
        "not a dict",
    ]
    result, stats = _parse_translation_json(json.dumps(data))
    assert len(result) == 1
    assert result[0]["key"] == "a"
    assert stats.skipped == 3


def test_parse_translation_json_not_array_raises():
    with pytest.raises(ValueError, match="JSON array"):
        _parse_translation_json(json.dumps({"key": "a", "target": "b"}))


def test_parse_translation_json_invalid_json_raises():
    with pytest.raises(Exception):
        _parse_translation_json("NOT JSON")


# ---------------------------------------------------------------------------
# _repair_invalid_escapes
# ---------------------------------------------------------------------------

def test_repair_invalid_escapes_backslash_N():
    raw = r'[{"key": "k", "target": "Hi \N[1]!"}]'
    repaired = _repair_invalid_escapes(raw)
    data = json.loads(repaired)
    assert data[0]["target"] == r"Hi \N[1]!"


def test_repair_invalid_escapes_backslash_V():
    raw = r'[{"key": "k", "target": "MP: \V[2]"}]'
    repaired = _repair_invalid_escapes(raw)
    data = json.loads(repaired)
    assert data[0]["target"] == r"MP: \V[2]"


def test_repair_invalid_escapes_backslash_C():
    raw = r'[{"key": "k", "target": "\C[3]Đỏ\C[0]"}]'
    repaired = _repair_invalid_escapes(raw)
    data = json.loads(repaired)
    assert data[0]["target"] == r"\C[3]Đỏ\C[0]"


def test_repair_invalid_escapes_does_not_touch_valid_escapes():
    raw = '[{"key": "k", "target": "line1\\nline2\\ttab"}]'
    repaired = _repair_invalid_escapes(raw)
    assert repaired == raw


def test_repair_invalid_escapes_does_not_touch_double_backslash():
    raw = r'[{"key": "k", "target": "path\\\\file"}]'
    repaired = _repair_invalid_escapes(raw)
    assert repaired == raw


def test_repair_invalid_escapes_multiple_codes_in_one_string():
    raw = r'[{"key": "k", "target": "\N[1] đánh \V[3] sát thương"}]'
    repaired = _repair_invalid_escapes(raw)
    data = json.loads(repaired)
    assert r"\N[1]" in data[0]["target"]
    assert r"\V[3]" in data[0]["target"]


# _parse_translation_json auto-repair integration

def test_parse_translation_json_auto_repairs_invalid_escape_backslash_N():
    """Exact case from production log: LLM emits \\N[1] as bare \\N[1] in JSON."""
    raw = r'[{"id": "Armors.json\u001f$[10].description", "key": "$[10].description", "target": "M\u00f3n qu\u00e0 Lisa t\u1eb7ng \N[1] m\u00e1t m\u1ebb."}]'
    result, stats = _parse_translation_json(raw)
    assert stats.parsed == 1
    assert r"\N[1]" in result[0]["target"]


def test_parse_translation_json_auto_repairs_multiple_rpg_codes():
    raw = r'[{"key": "k", "target": "\C[1]Tấn công\C[0] gây \V[2] sát thương!"}]'
    result, stats = _parse_translation_json(raw)
    assert stats.parsed == 1
    assert r"\C[1]" in result[0]["target"]
    assert r"\V[2]" in result[0]["target"]


def test_parse_translation_json_still_raises_on_truly_invalid_json():
    with pytest.raises(ValueError, match="not valid JSON"):
        _parse_translation_json('[{"key": "k", "target": BROKEN}')




# ---------------------------------------------------------------------------
# _parse_google_translate_response
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# _chat_completion_text
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# _anthropic_message_text
# ---------------------------------------------------------------------------

def test_anthropic_message_text_basic():
    message = SimpleNamespace(content=[SimpleNamespace(type="text", text="[]")])
    assert _anthropic_message_text(message) == "[]"


def test_anthropic_message_text_none_raises_clear_error():
    with pytest.raises(ValueError, match="returned no message"):
        _anthropic_message_text(None)


def test_anthropic_message_text_missing_content_raises_clear_error():
    message = SimpleNamespace(content=None)
    with pytest.raises(ValueError, match="returned no content"):
        _anthropic_message_text(message)


def test_anthropic_message_text_empty_text_raises_clear_error():
    message = SimpleNamespace(content=[SimpleNamespace(type="thinking", text="ignored")])
    with pytest.raises(ValueError, match="empty text content"):
        _anthropic_message_text(message)


def test_chat_completion_text_basic():
    response = SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content="[]"))])
    assert _chat_completion_text(response) == "[]"


def test_chat_completion_text_empty_choices_raises_clear_error():
    response = SimpleNamespace(choices=[])
    with pytest.raises(ValueError, match="returned no choices"):
        _chat_completion_text(response)


def test_chat_completion_text_missing_content_raises_clear_error():
    response = SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=None))])
    with pytest.raises(ValueError, match="returned no message content"):
        _chat_completion_text(response)


def test_parse_google_translate_response_basic():
    data = [[["Xin", "Hello", None, None], [" chào", " world", None, None]], None, "en"]
    assert _parse_google_translate_response(data) == "Xin chào"


def test_parse_google_translate_response_empty_or_malformed_returns_empty():
    assert _parse_google_translate_response([]) == ""
    assert _parse_google_translate_response([None]) == ""
    assert _parse_google_translate_response([[[]]]) == ""


def test_parse_translation_json_empty_array():
    result, stats = _parse_translation_json("[]")
    assert result == []
    assert stats.parsed == 0


def test_user_prompt_includes_file_key_id():
    entry = TextEntry(Path("data/Actors.json"), "$[1].name", "Harold")
    payload = json.loads(_user_prompt([entry], "Vietnamese", None))

    item = payload["items"][0]
    assert item["id"] == "data/Actors.json\x1f$[1].name"
    assert item["file"] == "data/Actors.json"
    assert item["key"] == "$[1].name"


def test_user_prompt_auto_detect_japanese():
    entry = TextEntry(Path("data/Map001.json"), "$[1].name", "こんにちは")
    payload = json.loads(_user_prompt([entry], "Vietnamese", None))
    assert payload["source_language"] == "ja"


def test_user_prompt_auto_detect_korean():
    entry = TextEntry(Path("data/Map001.json"), "$[1].name", "안녕하세요")
    payload = json.loads(_user_prompt([entry], "Vietnamese", None))
    assert payload["source_language"] == "ko"


def test_user_prompt_auto_detect_chinese():
    entry = TextEntry(Path("data/Map001.json"), "$[1].name", "你好世界")
    payload = json.loads(_user_prompt([entry], "Vietnamese", None))
    assert payload["source_language"] == "zh"


def test_user_prompt_auto_detect_korean_before_chinese():
    """Korean text should be detected as 'ko', not 'zh'."""
    entry = TextEntry(Path("data/Map001.json"), "$[1].name", "게임 시작")
    payload = json.loads(_user_prompt([entry], "Vietnamese", None))
    assert payload["source_language"] == "ko"


def test_results_from_json_uses_id_for_duplicate_keys():
    entries = [
        TextEntry(Path("Actors.json"), "$[1].name", "Harold"),
        TextEntry(Path("Items.json"), "$[1].name", "Potion"),
    ]
    response = json.dumps([
        {"id": "Actors.json\x1f$[1].name", "key": "$[1].name", "target": "Ha-rôn"},
        {"id": "Items.json\x1f$[1].name", "key": "$[1].name", "target": "Thuốc"},
    ])

    results, _ = _results_from_json(entries, response)

    assert [result.target for result in results] == ["Ha-rôn", "Thuốc"]


def test_results_from_json_keeps_key_fallback_for_single_file():
    entry = TextEntry(Path("Actors.json"), "$[1].name", "Harold")
    response = json.dumps([{"key": "$[1].name", "target": "Ha-rôn"}])

    results, _ = _results_from_json([entry], response)
    assert results[0].target == "Ha-rôn"


def test_results_from_json_no_key_fallback_across_files():
    entries = [
        TextEntry(Path("Actors.json"), "$[1].name", "Harold"),
        TextEntry(Path("Items.json"), "$[1].name", "Potion"),
    ]
    response = json.dumps([{"key": "$[1].name", "target": "Wrong"}])
    results, _ = _results_from_json(entries, response)
    assert results[0].target == "Harold"
    assert results[1].target == "Potion"


# ---------------------------------------------------------------------------
# _fix_token_formatting
# ---------------------------------------------------------------------------

def test_fix_token_formatting_bracket_with_spaces():
    assert _fix_token_formatting(r"\N [1]") == r"\N[1]"
    assert _fix_token_formatting(r"\V[ 2 ]") == r"\V[2]"
    assert _fix_token_formatting(r"\C[  3  ]") == r"\C[3]"


def test_fix_token_formatting_angle_bracket_with_spaces():
    assert _fix_token_formatting(r"\n< text >") == r"\n<text>"


def test_fix_token_formatting_percent_with_space():
    assert _fix_token_formatting("% 1 damage") == "%1 damage"
    assert _fix_token_formatting("%  2") == "%2"


def test_fix_token_formatting_backslash_special_chars():
    # LLM inserts a space after backslash: "\ !" → "\!"
    assert _fix_token_formatting("\\ !") == "\\!"
    assert _fix_token_formatting("\\ >") == "\\>"
    assert _fix_token_formatting("\\ {") == "\\{"


def test_fix_token_formatting_no_change_when_correct():
    s = r"\N[1] and \V[2] deal %1 damage"
    assert _fix_token_formatting(s) == s


def test_fix_token_formatting_preserves_plain_text():
    s = "Hello, world! 100% sure."
    assert _fix_token_formatting(s) == s


def test_fix_token_formatting_applied_in_results_from_json():
    """_results_from_json must auto-fix token spacing via _fix_token_formatting."""
    entries = [TextEntry(Path("a.json"), "$.k", r"\N[1] says hi")]
    # LLM returned broken spacing
    response = json.dumps([{"id": "a.json::$.k", "key": "$.k", "target": r"\N [1] says hi"}])
    results, _ = _results_from_json(entries, response)
    assert results[0].target == r"\N[1] says hi"


# ---------------------------------------------------------------------------
# glossary.load_correction_table / apply_correction_table
# ---------------------------------------------------------------------------

def test_load_correction_table_basic(tmp_path):
    from game_llm_translator.glossary import load_correction_table, apply_correction_table
    p = tmp_path / "ct.csv"
    p.write_text("find,replace\nMP,Ma lực\nHP,Sinh lực\n", encoding="utf-8")
    rules = load_correction_table(p)
    assert rules == [("MP", "Ma lực"), ("HP", "Sinh lực")]


def test_load_correction_table_missing_file(tmp_path):
    from game_llm_translator.glossary import load_correction_table
    assert load_correction_table(tmp_path / "nonexistent.csv") == []


def test_load_correction_table_allows_empty_replace(tmp_path):
    from game_llm_translator.glossary import load_correction_table
    p = tmp_path / "ct.csv"
    p.write_text("find,replace\nbadword,\n", encoding="utf-8")
    rules = load_correction_table(p)
    assert rules == [("badword", "")]


def test_apply_correction_table_replaces_all_occurrences():
    from game_llm_translator.glossary import apply_correction_table
    rules = [("MP", "Ma lực"), ("HP", "Sinh lực")]
    result = apply_correction_table("Mất 10 MP và 5 HP", rules)
    assert result == "Mất 10 Ma lực và 5 Sinh lực"


def test_apply_correction_table_empty_rules():
    from game_llm_translator.glossary import apply_correction_table
    assert apply_correction_table("unchanged text", []) == "unchanged text"


def test_apply_correction_table_delete_term():
    from game_llm_translator.glossary import apply_correction_table
    rules = [("unwanted", "")]
    assert apply_correction_table("remove unwanted word", rules) == "remove  word"


# ---------------------------------------------------------------------------
# extract_namebox_names
# ---------------------------------------------------------------------------

def test_extract_namebox_names_simple():
    """Extract CJK name from simple \\n<Name> pattern."""
    entries = [TextEntry(Path("Map001.json"), "$[1].parameters[0]", "\\n<ディオン>「相変わらずだな」")]
    names = extract_namebox_names(entries)
    assert "ディオン" in names


def test_extract_namebox_names_with_color_code():
    """Extract visible name from \\n<\\C[0]長老> — strip control codes."""
    entries = [TextEntry(Path("Map001.json"), "$[1].parameters[0]", "\\n<\\C[0]長老>「安心したまえ」")]
    names = extract_namebox_names(entries)
    assert "長老" in names


def test_extract_namebox_names_dedup():
    """Same name in multiple entries should appear only once."""
    entries = [
        TextEntry(Path("Map001.json"), "$[1].parameters[0]", "\\n<ディオン>line1"),
        TextEntry(Path("Map001.json"), "$[2].parameters[0]", "\\n<ディオン>line2"),
    ]
    names = extract_namebox_names(entries)
    assert len(names) == 1
    assert "ディオン" in names


def test_extract_namebox_names_skips_non_cjk():
    """Non-CJK names like \\n<Alice> should not be extracted."""
    entries = [TextEntry(Path("Map001.json"), "$[1].parameters[0]", "\\n<Alice>Hello")]
    names = extract_namebox_names(entries)
    assert names == {}


def test_extract_namebox_names_no_namebox():
    """Entries without namebox prefix should be skipped."""
    entries = [TextEntry(Path("Map001.json"), "$[1].parameters[0]", "Just some dialogue")]
    names = extract_namebox_names(entries)
    assert names == {}


# ---------------------------------------------------------------------------
# _replace_untranslated_namebox_names
# ---------------------------------------------------------------------------

def test_replace_untranslated_namebox_simple():
    """CJK name in namebox should be replaced with its translation."""
    source = "\\n<ディオン>「相変わらずだな」"
    target = "\\n<ディオン>「Vẫn giỏi như mọi khi」"
    translations = {"ディオン": "Dion"}
    result = _replace_untranslated_namebox_names(target, source, translations)
    assert "Dion" in result
    assert "ディオン" not in result.split("<")[1].split(">")[0]


def test_replace_untranslated_namebox_with_color_code():
    """CJK name with color code should be replaced while keeping color code."""
    source = "\\n<\\C[0]長老>「安心したまえ」"
    target = "\\n<\\C[0]長老>「Hãy yên tâm」"
    translations = {"長老": "Trưởng lão"}
    result = _replace_untranslated_namebox_names(target, source, translations)
    assert "Trưởng lão" in result
    assert "\\C[0]" in result
    assert "長老" not in result.split("<")[1].split(">")[0]


def test_replace_untranslated_namebox_already_translated():
    """If name is already translated, don't change it."""
    source = "\\n<ディオン>「相変わらずだな」"
    target = "\\n<Dion>「Vẫn giỏi như mọi khi」"
    translations = {"ディオン": "Dion"}
    result = _replace_untranslated_namebox_names(target, source, translations)
    assert result == target


def test_replace_untranslated_namebox_no_namebox():
    """If target has no namebox prefix, return unchanged."""
    source = "\\n<ディオン>「相変わらずだな」"
    target = "Vẫn giỏi như mọi khi"
    translations = {"ディオン": "Dion"}
    result = _replace_untranslated_namebox_names(target, source, translations)
    assert result == target


def test_replace_untranslated_namebox_no_translations():
    """If translations dict is empty, return unchanged."""
    source = "\\n<ディオン>「相変わらずだな」"
    target = "\\n<ディオン>「Vẫn giỏi như mọi khi」"
    result = _replace_untranslated_namebox_names(target, source, {})
    assert result == target


def test_replace_untranslated_namebox_face_prefix():
    """Name with face+control-code prefix like \\F[N_01]\\n<希>."""
    source = "\\F[N_01]\\n<希>こんにちは"
    target = "\\F[N_01]\\n<希>Xin chào"
    translations = {"希": "Hi"}
    result = _replace_untranslated_namebox_names(target, source, translations)
    assert "<Hi>" in result


# ---------------------------------------------------------------------------
# _parse_name_json
# ---------------------------------------------------------------------------

def test_parse_name_json_object():
    """LLM returns JSON object: {"ディオン": "Dion", "長老": "Elder"}."""
    names = {"ディオン": "ディオン", "長老": "長老"}
    text = '{"ディオン": "Dion", "長老": "Elder"}'
    result = _parse_name_json(text, names)
    assert result == {"ディオン": "Dion", "長老": "Elder"}


def test_parse_name_json_with_code_fence():
    """LLM wraps JSON in markdown code fence."""
    names = {"ディオン": "ディオン"}
    text = '```json\n{"ディオン": "Dion"}\n```'
    result = _parse_name_json(text, names)
    assert result == {"ディオン": "Dion"}


def test_parse_name_json_array_fallback():
    """LLM returns array of dicts with 'original'/'translation' keys."""
    names = {"ディオン": "ディオン", "長老": "長老"}
    text = '[{"original": "ディオン", "translation": "Dion"}, {"original": "長老", "translation": "Elder"}]'
    result = _parse_name_json(text, names)
    assert result == {"ディオン": "Dion", "長老": "Elder"}


def test_parse_name_json_ignores_unknown_names():
    """Names not in the input dict are ignored."""
    names = {"ディオン": "ディオン"}
    text = '{"ディオン": "Dion", "サクラ": "Sakura"}'
    result = _parse_name_json(text, names)
    assert result == {"ディオン": "Dion"}
    assert "サクラ" not in result


def test_parse_name_json_invalid_json():
    """Invalid JSON returns empty dict."""
    names = {"ディオン": "ディオン"}
    result = _parse_name_json("NOT JSON", names)
    assert result == {}


def test_parse_name_json_empty_translation_skipped():
    """Empty translation strings are skipped."""
    names = {"ディオン": "ディオン"}
    text = '{"ディオン": "  "}'
    result = _parse_name_json(text, names)
    assert result == {}
