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
    _results_from_json,
    _user_prompt,
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


def test_restore_case_insensitive_token():
    """If LLM lowercases the placeholder token, it should still be restored."""
    text = "\\V[1]"
    masked, mapping = _mask_protected_tokens(text)
    lowercased = masked.lower()
    restored = _restore_protected_tokens(lowercased, mapping)
    assert "\\V[1]" in restored


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
