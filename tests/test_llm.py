from __future__ import annotations

import json

from pathlib import Path

import pytest

from game_llm_translator.llm import (
    _lang_code,
    _mask_protected_tokens,
    _parse_translation_json,
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


def test_lang_code_unknown_truncates_to_two():
    assert _lang_code("unknown_lang") == "un"


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
    result = _parse_translation_json(json.dumps(data))
    assert result == data


def test_parse_translation_json_strips_code_fence():
    raw = "```json\n[{\"key\": \"a\", \"target\": \"b\"}]\n```"
    result = _parse_translation_json(raw)
    assert result[0]["target"] == "b"


def test_parse_translation_json_strips_plain_fence():
    raw = "```\n[{\"key\": \"a\", \"target\": \"b\"}]\n```"
    result = _parse_translation_json(raw)
    assert result[0]["target"] == "b"


def test_parse_translation_json_filters_incomplete():
    data = [
        {"key": "a", "target": "b"},
        {"key": "c"},          # missing "target"
        {"target": "d"},       # missing "key"
        "not a dict",
    ]
    result = _parse_translation_json(json.dumps(data))
    assert len(result) == 1
    assert result[0]["key"] == "a"


def test_parse_translation_json_not_array_raises():
    with pytest.raises(ValueError, match="JSON array"):
        _parse_translation_json(json.dumps({"key": "a", "target": "b"}))


def test_parse_translation_json_invalid_json_raises():
    with pytest.raises(Exception):
        _parse_translation_json("NOT JSON")


def test_parse_translation_json_empty_array():
    result = _parse_translation_json("[]")
    assert result == []


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

    results = _results_from_json(entries, response)

    assert [result.target for result in results] == ["Ha-rôn", "Thuốc"]


def test_results_from_json_keeps_key_fallback_for_older_responses():
    entry = TextEntry(Path("Actors.json"), "$[1].name", "Harold")
    response = json.dumps([{"key": "$[1].name", "target": "Ha-rôn"}])

    assert _results_from_json([entry], response)[0].target == "Ha-rôn"
