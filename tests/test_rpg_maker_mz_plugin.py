from __future__ import annotations

import json
from pathlib import Path

import pytest

from game_llm_translator.rpg_maker_mz import (
    _extract_mz_plugin_text,
    _looks_like_translatable_text,
)


# ---------------------------------------------------------------------------
# _looks_like_translatable_text
# ---------------------------------------------------------------------------

def test_looks_like_text_normal_sentence():
    assert _looks_like_translatable_text("Welcome to the village!") is True


def test_looks_like_text_japanese():
    assert _looks_like_translatable_text("勇者よ、立ち上がれ！") is True


def test_looks_like_text_vietnamese():
    assert _looks_like_translatable_text("Chào mừng bạn đến với làng!") is True


def test_looks_like_text_rejects_empty():
    assert _looks_like_translatable_text("") is False
    assert _looks_like_translatable_text("   ") is False


def test_looks_like_text_rejects_single_char():
    assert _looks_like_translatable_text("A") is False


def test_looks_like_text_rejects_hex_color():
    assert _looks_like_translatable_text("#ff0000") is False
    assert _looks_like_translatable_text("#FFF") is False
    assert _looks_like_translatable_text("#aabbccdd") is False


def test_looks_like_text_rejects_numeric():
    assert _looks_like_translatable_text("42") is False
    assert _looks_like_translatable_text("-3.14") is False
    assert _looks_like_translatable_text("0") is False


def test_looks_like_text_rejects_asset_filename():
    assert _looks_like_translatable_text("Actor1.png") is False
    assert _looks_like_translatable_text("battle_bgm.ogg") is False
    assert _looks_like_translatable_text("Map001.json") is False


def test_looks_like_text_rejects_variable_like_identifier():
    assert _looks_like_translatable_text("myVariable") is False
    assert _looks_like_translatable_text("PluginName") is False
    assert _looks_like_translatable_text("camelCaseId") is False


def test_looks_like_text_rejects_no_letters():
    assert _looks_like_translatable_text("123-456") is False
    assert _looks_like_translatable_text("!@#$") is False


def test_looks_like_text_accepts_short_readable_with_spaces():
    assert _looks_like_translatable_text("OK sure") is True


def test_looks_like_text_accepts_rpg_control_code_text():
    assert _looks_like_translatable_text(r"Hello \N[1]!") is True


# ---------------------------------------------------------------------------
# _extract_mz_plugin_text — basic extraction
# ---------------------------------------------------------------------------

FILE = Path("Map001.json")


def _cmd(plugin: str, command_name: str, args: dict) -> dict:
    return {"code": 357, "parameters": [plugin, command_name, "", args]}


def test_extracts_text_field_from_unknown_plugin():
    cmd = _cmd("SomePlugin", "show", {"text": "Hello world"})
    entries = _extract_mz_plugin_text(cmd, FILE, "$[0].list[0]", "ctx")
    assert len(entries) == 1
    assert entries[0].source == "Hello world"
    assert entries[0].context == "rpg_maker_mz_plugin_SomePlugin_show_text"


def test_extracts_message_field():
    cmd = _cmd("YEP_MessageCore", "show", {"message": "Quest complete!"})
    entries = _extract_mz_plugin_text(cmd, FILE, "$[0].list[0]", "ctx")
    assert len(entries) == 1
    assert entries[0].source == "Quest complete!"


def test_extracts_multiple_text_fields():
    cmd = _cmd("SomePlugin", "display", {"title": "Chapter 1", "body": "The story begins..."})
    entries = _extract_mz_plugin_text(cmd, FILE, "$[0].list[0]", "ctx")
    sources = {e.source for e in entries}
    assert "Chapter 1" in sources
    assert "The story begins..." in sources


def test_skips_non_357_code():
    cmd = {"code": 401, "parameters": [0, "SomePlugin", "show", {"text": "Hello"}]}
    entries = _extract_mz_plugin_text(cmd, FILE, "$", "ctx")
    assert entries == []


def test_skips_missing_parameters():
    cmd = {"code": 357, "parameters": ["Plugin", "cmd"]}
    entries = _extract_mz_plugin_text(cmd, FILE, "$", "ctx")
    assert entries == []


def test_skips_parameters3_not_dict():
    cmd = {"code": 357, "parameters": ["Plugin", "cmd", "", "notadict"]}
    entries = _extract_mz_plugin_text(cmd, FILE, "$", "ctx")
    assert entries == []


def test_skips_blacklisted_field_filename():
    cmd = _cmd("PicPlugin", "show", {"filename": "Actor1.png", "text": "Hello!"})
    entries = _extract_mz_plugin_text(cmd, FILE, "$", "ctx")
    sources = [e.source for e in entries]
    assert "Actor1.png" not in sources
    assert "Hello!" in sources


def test_skips_blacklisted_field_color():
    cmd = _cmd("MsgPlugin", "set", {"color": "#ff0000", "text": "Attack!"})
    entries = _extract_mz_plugin_text(cmd, FILE, "$", "ctx")
    sources = [e.source for e in entries]
    assert "#ff0000" not in sources
    assert "Attack!" in sources


def test_skips_numeric_string_values():
    cmd = _cmd("PluginA", "set", {"opacity": "255", "label": "Health bar"})
    entries = _extract_mz_plugin_text(cmd, FILE, "$", "ctx")
    sources = [e.source for e in entries]
    assert "255" not in sources
    assert "Health bar" in sources


def test_skips_identifier_like_values():
    cmd = _cmd("PluginB", "run", {"mode": "attackMode", "description": "A fierce attack!"})
    entries = _extract_mz_plugin_text(cmd, FILE, "$", "ctx")
    sources = [e.source for e in entries]
    assert "attackMode" not in sources
    assert "A fierce attack!" in sources


def test_skips_non_string_values():
    cmd = _cmd("PluginC", "set", {"count": 5, "enabled": True, "label": "Ready"})
    entries = _extract_mz_plugin_text(cmd, FILE, "$", "ctx")
    assert len(entries) == 1
    assert entries[0].source == "Ready"


def test_key_format_includes_field_name():
    cmd = _cmd("TextPicture", "set", {"text": "Hello world"})
    entries = _extract_mz_plugin_text(cmd, FILE, "$[1].list[0]", "ctx")
    assert entries[0].key == "$[1].list[0].parameters[3].text"


def test_context_text_passed_through():
    cmd = _cmd("SomePlugin", "show", {"text": "Fight!"})
    entries = _extract_mz_plugin_text(cmd, FILE, "$", "battle scene context")
    assert entries[0].context_text == "battle scene context"


def test_backward_compat_textpicture_set():
    """TextPicture/set (original hardcoded case) must still be extracted."""
    cmd = _cmd("TextPicture", "set", {"text": "Displayed on screen"})
    entries = _extract_mz_plugin_text(cmd, FILE, "$", "ctx")
    assert len(entries) == 1
    assert entries[0].source == "Displayed on screen"


def test_empty_args_dict_returns_nothing():
    cmd = _cmd("EmptyPlugin", "noop", {})
    entries = _extract_mz_plugin_text(cmd, FILE, "$", "ctx")
    assert entries == []


def test_japanese_text_extracted():
    cmd = _cmd("VisuMZ_1_MessageCore", "show", {"text": "勇者よ、立ち上がれ！"})
    entries = _extract_mz_plugin_text(cmd, FILE, "$", "ctx")
    assert len(entries) == 1
    assert entries[0].source == "勇者よ、立ち上がれ！"
