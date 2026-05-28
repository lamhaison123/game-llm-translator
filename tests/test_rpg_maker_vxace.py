"""Tests for RPG Maker VX Ace (.rvdata2 Ruby Marshal) extract + apply.

We build synthetic Marshal binaries in-test by constructing ME trees with the
vendored codec, then run the public API against the dumped bytes. This avoids
the need to vendor real game fixtures.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from game_llm_translator.models import TranslationResult
from game_llm_translator.rpg_maker_vxace import (
    _parse_path,
    _resolve_path,
    apply_rpg_maker_vxace,
    extract_rpg_maker_vxace,
)
from game_llm_translator.vendor.rm_marshal_core import MC, ME


# ---- ME construction helpers ----

def _str_me(mc: MC, value: str) -> ME:
    """Build a Marshal string ME with UTF-8 encoding ivar (E=true)."""
    s = ME(mc, b'"', value.encode("utf-8"))
    e_key = ME(mc, b":", b"E")
    mc.symtable.append(e_key)
    e_val = ME(mc, b"T", True)
    table = ME(mc, b"{", {b"E": (e_key, e_val)})
    table.silent_token = True
    s.attributes = table
    return s


def _int_me(mc: MC, value: int) -> ME:
    return ME(mc, b"i", value)


def _nil_me(mc: MC) -> ME:
    return ME(mc, b"0", None)


def _array_me(mc: MC, items: list[ME]) -> ME:
    return ME(mc, b"[", items)


def _hash_me(mc: MC, pairs: list[tuple[object, ME, ME]]) -> ME:
    """Build a Marshal hash ME. `pairs` is list of (raw_key, key_me, value_me)."""
    table: dict = {raw_key: (key_me, value_me) for raw_key, key_me, value_me in pairs}
    return ME(mc, b"{", table)


def _sym_me(mc: MC, value: str) -> ME:
    sym = ME(mc, b":", value.encode("utf-8"))
    mc.symtable.append(sym)
    return sym


def _object_me(mc: MC, class_name: str, ivars: list[tuple[str, ME]]) -> ME:
    """Build an RPG::Foo object ME with the given instance variables."""
    sym = _sym_me(mc, class_name)
    pairs: list[tuple[object, ME, ME]] = []
    for ivar_name, value_me in ivars:
        raw_key = f"@{ivar_name}".encode("utf-8")
        key_me = _sym_me(mc, f"@{ivar_name}")
        pairs.append((raw_key, key_me, value_me))
    table = _hash_me(mc, pairs)
    table.silent_token = True
    return ME(mc, b"o", (sym, table))


def _build(mc: MC, root: ME) -> bytes:
    mc.root = root
    return mc.dump()


# ---- fixtures ----

def _make_actors_file(actors: list[dict]) -> bytes:
    mc = MC()
    items: list[ME] = [_nil_me(mc)]
    for actor in actors:
        ivars: list[tuple[str, ME]] = []
        for k in ("name", "nickname", "description", "note"):
            if k in actor:
                ivars.append((k, _str_me(mc, actor[k])))
        items.append(_object_me(mc, "RPG::Actor", ivars))
    return _build(mc, _array_me(mc, items))


def _make_system_file(terms_basic: list[str] | None = None,
                     terms_commands: list[str] | None = None,
                     elements: list[str] | None = None,
                     game_title: str = "テストゲーム") -> bytes:
    mc = MC()
    terms_ivars: list[tuple[str, ME]] = []
    if terms_basic is not None:
        terms_ivars.append(("basic", _array_me(mc, [_str_me(mc, s) for s in terms_basic])))
    if terms_commands is not None:
        terms_ivars.append(("commands", _array_me(mc, [_str_me(mc, s) for s in terms_commands])))
    terms_obj = _object_me(mc, "RPG::System::Terms", terms_ivars)
    sys_ivars: list[tuple[str, ME]] = [
        ("game_title", _str_me(mc, game_title)),
        ("terms", terms_obj),
    ]
    if elements is not None:
        sys_ivars.append(("elements", _array_me(mc, [_str_me(mc, s) for s in elements])))
    return _build(mc, _object_me(mc, "RPG::System", sys_ivars))


def _make_event_command(mc: MC, code: int, parameters: list[ME]) -> ME:
    return _object_me(mc, "RPG::EventCommand", [
        ("code", _int_me(mc, code)),
        ("indent", _int_me(mc, 0)),
        ("parameters", _array_me(mc, parameters)),
    ])


def _make_map_file(commands: list[tuple[int, list]]) -> bytes:
    """Build a minimal Map###.rvdata2 with a single event containing the given commands.

    Each command is (code, parameters_as_python_values). String values become
    Marshal strings; list values become Marshal arrays of strings; int values become fixnums.
    """
    mc = MC()
    cmd_mes: list[ME] = []
    for code, params in commands:
        param_mes: list[ME] = []
        for p in params:
            if isinstance(p, str):
                param_mes.append(_str_me(mc, p))
            elif isinstance(p, list):
                param_mes.append(_array_me(mc, [_str_me(mc, s) for s in p]))
            elif isinstance(p, int):
                param_mes.append(_int_me(mc, p))
            elif p is None:
                param_mes.append(_nil_me(mc))
        cmd_mes.append(_make_event_command(mc, code, param_mes))
    page = _object_me(mc, "RPG::Event::Page", [
        ("list", _array_me(mc, cmd_mes)),
    ])
    event = _object_me(mc, "RPG::Event", [
        ("id", _int_me(mc, 1)),
        ("name", _str_me(mc, "イベント1")),
        ("pages", _array_me(mc, [page])),
    ])
    map_obj = _object_me(mc, "RPG::Map", [
        ("display_name", _str_me(mc, "テストマップ")),
        ("note", _str_me(mc, "")),
        ("events", _hash_me(mc, [(1, _int_me(mc, 1), event)])),
    ])
    return _build(mc, map_obj)


def _write_data_files(tmp: Path, files: dict[str, bytes]) -> Path:
    data = tmp / "Data"
    data.mkdir()
    for name, content in files.items():
        (data / name).write_bytes(content)
    return tmp


# ---- tests ----

def test_path_parse_attrs_and_indexes():
    steps = _parse_path("$.events[1].pages[0].list[12].parameters[0]")
    assert steps == [
        ("attr", "events"), ("idx", 1),
        ("attr", "pages"), ("idx", 0),
        ("attr", "list"), ("idx", 12),
        ("attr", "parameters"), ("idx", 0),
    ]


def test_path_parse_rejects_bad_paths():
    with pytest.raises(ValueError):
        _parse_path("events[0]")
    with pytest.raises(ValueError):
        _parse_path("$.foo bar")


def test_extract_actors_skips_first_nil_and_notes_separated(tmp_path: Path):
    payload = _make_actors_file([
        {"name": "太郎", "nickname": "勇者", "description": "勇敢な若者", "note": "<class:0>"},
        {"name": "花子", "nickname": "魔法使い", "description": "賢い", "note": "ただのメモ書きです"},
    ])
    _write_data_files(tmp_path, {"Actors.rvdata2": payload})
    entries = extract_rpg_maker_vxace(tmp_path)

    contexts = {e.context for e in entries}
    keys = {e.key for e in entries}
    # nil at index 0 must be skipped — actors live at index 1+
    assert "$[0].name" not in keys
    assert "$[1].name" in keys
    assert "$[2].name" in keys

    # All non-note fields use database-specific contexts
    name_entry = next(e for e in entries if e.key == "$[1].name")
    assert name_entry.context == "rpg_maker_actors_name"
    assert name_entry.source == "太郎"

    nickname_entry = next(e for e in entries if e.key == "$[1].nickname")
    assert nickname_entry.context == "rpg_maker_actors_nickname"

    # @note with a script-tag-only value (CJK chars present though — "class" is just ASCII)
    # gets extracted only if it has CJK content. <class:0> has no CJK → not extracted.
    assert "$[1].note" not in keys
    # The real-prose note ("ただのメモ書きです") IS extracted with rpg_maker_vxace_note context
    note_entry = next(e for e in entries if e.key == "$[2].note")
    assert note_entry.context == "rpg_maker_vxace_note"


def test_extract_map_dialogue_and_choices(tmp_path: Path):
    payload = _make_map_file([
        (101, ["", 0, 0, 2]),           # header — no text extracted in VX Ace
        (401, ["こんにちは、世界！"]),    # dialogue line
        (401, ["どこへ行きますか？"]),
        (102, [["はい", "いいえ"], 0]),  # choices
        (402, [0, "はい"]),              # choice label (mirror)
        (108, ["デバッグコメント"]),     # comment
        (320, [1, "新しい名前"]),         # change actor name
        (655, ['p "怪しい影"']),           # script line with CJK
    ])
    _write_data_files(tmp_path, {"Map001.rvdata2": payload})

    entries = extract_rpg_maker_vxace(tmp_path)
    by_key = {e.key: e for e in entries}

    # 101 has no text — must NOT be extracted at parameters[4]
    assert "$.events[1].pages[0].list[0].parameters[4]" not in by_key

    # 401 dialogue lines
    dialogue1 = by_key["$.events[1].pages[0].list[1].parameters[0]"]
    assert dialogue1.source == "こんにちは、世界！"
    assert dialogue1.context == "rpg_maker_map_dialogue"

    # 102 choices: parameters[0][0], parameters[0][1]
    choice_yes = by_key["$.events[1].pages[0].list[3].parameters[0][0]"]
    assert choice_yes.source == "はい"
    assert choice_yes.context == "rpg_maker_map_choice"

    # 402 choice label
    choice_label = by_key["$.events[1].pages[0].list[4].parameters[1]"]
    assert choice_label.source == "はい"
    assert choice_label.context == "rpg_maker_map_choice_label"

    # 108 comment
    comment = by_key["$.events[1].pages[0].list[5].parameters[0]"]
    assert comment.context == "rpg_maker_map_comment"

    # 320 change actor name
    name_change = by_key["$.events[1].pages[0].list[6].parameters[1]"]
    assert name_change.source == "新しい名前"
    assert name_change.context == "rpg_maker_map_actor_name"

    # 655 script
    script = by_key["$.events[1].pages[0].list[7].parameters[0]"]
    assert script.context == "rpg_maker_map_script"


def test_extract_system_terms_and_arrays(tmp_path: Path):
    payload = _make_system_file(
        terms_basic=["レベル", "Lv", "体力", "HP", "魔力", "MP", "技力", "TP"],
        terms_commands=["戦う", "逃げる"],
        elements=["", "物理", "炎", "氷"],
        game_title="僕のゲーム",
    )
    _write_data_files(tmp_path, {"System.rvdata2": payload})
    entries = extract_rpg_maker_vxace(tmp_path)
    by_key = {e.key: (e.source, e.context) for e in entries}

    assert by_key["$.game_title"] == ("僕のゲーム", "rpg_maker_system_game_title")
    assert by_key["$.terms.basic[0]"] == ("レベル", "rpg_maker_terms_basic")
    assert by_key["$.terms.basic[2]"] == ("体力", "rpg_maker_terms_basic")
    assert by_key["$.terms.commands[0]"] == ("戦う", "rpg_maker_terms_commands")
    assert by_key["$.elements[1]"] == ("物理", "rpg_maker_system_element")
    # ASCII-only string "Lv" has no CJK char and should be skipped
    assert "$.terms.basic[1]" not in by_key
    # Empty element string at [0] must be skipped
    assert "$.elements[0]" not in by_key


def test_apply_roundtrip_changes_only_target_strings(tmp_path: Path):
    payload = _make_actors_file([
        {"name": "太郎", "nickname": "勇者", "description": "若い戦士"},
        {"name": "花子", "nickname": "魔法使い", "description": "賢者"},
    ])
    src = tmp_path / "Data" / "Actors.rvdata2"
    src.parent.mkdir()
    src.write_bytes(payload)
    entries = extract_rpg_maker_vxace(tmp_path)
    # Translate just one name
    target = next(e for e in entries if e.key == "$[1].name")
    results = [
        TranslationResult(file=target.file, key=target.key, source=target.source, target="Taro", context=target.context),
    ]
    out = tmp_path / "out"
    apply_rpg_maker_vxace(results, out)

    # Reload + verify
    written = (out / "Actors.rvdata2").read_bytes()
    mc = MC.load(written)
    actors = mc.root
    # actors is array, [0]=nil, [1]=first actor
    actor1 = actors.data[1].at()
    actor2 = actors.data[2].at()
    name1 = actor1.data[1].data[b"@name"][1].at().data.decode("utf-8")
    nick1 = actor1.data[1].data[b"@nickname"][1].at().data.decode("utf-8")
    name2 = actor2.data[1].data[b"@name"][1].at().data.decode("utf-8")
    assert name1 == "Taro"      # translated
    assert nick1 == "勇者"       # untouched
    assert name2 == "花子"       # untouched


def test_apply_skips_unknown_paths_without_corrupting_file(tmp_path: Path):
    payload = _make_actors_file([{"name": "太郎"}])
    src = tmp_path / "Data" / "Actors.rvdata2"
    src.parent.mkdir()
    src.write_bytes(payload)

    results = [
        TranslationResult(file=src, key="$[1].name", source="太郎", target="Taro"),
        TranslationResult(file=src, key="$[999].name", source="太郎", target="LostInVoid"),
    ]
    out = tmp_path / "out"
    apply_rpg_maker_vxace(results, out)
    written = (out / "Actors.rvdata2").read_bytes()
    mc = MC.load(written)
    name = mc.root.data[1].at().data[1].data[b"@name"][1].at().data.decode("utf-8")
    assert name == "Taro"


def test_round_trip_byte_equal_for_unchanged_string(tmp_path: Path):
    payload = _make_actors_file([{"name": "太郎", "nickname": "勇者"}])
    # Round-trip through MC
    mc = MC.load(payload)
    redumped = mc.dump()
    assert redumped == payload


def test_extract_skips_scripts_rvdata2(tmp_path: Path):
    # Even if a "Scripts.rvdata2" file exists, we don't try to extract from it.
    fake = b"\x04\x08\"\x06A"  # tiny invalid-ish payload
    _write_data_files(tmp_path, {"Scripts.rvdata2": fake})
    # Should return empty without raising — Scripts file is intentionally skipped.
    entries = extract_rpg_maker_vxace(tmp_path)
    assert entries == []
