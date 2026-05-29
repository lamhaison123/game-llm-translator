"""Tests for RPG Maker VX Ace (.rvdata2 Ruby Marshal) extract + apply.

We build synthetic Marshal binaries in-test by constructing ME trees with the
vendored codec, then run the public API against the dumped bytes. This avoids
the need to vendor real game fixtures.
"""
from __future__ import annotations

import zlib
from pathlib import Path

import pytest

from game_llm_translator.models import TranslationResult
from game_llm_translator.rpg_maker_vxace import (
    _note_extract_mode,
    _parse_path,
    _replace_ruby_string_literal,
    _resolve_path,
    _ruby_string_literals,
    _decode_script_source,
    _script_record_parts,
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


def _make_scripts_file(scripts: list[tuple[str, str]]) -> bytes:
    mc = MC()
    items: list[ME] = []
    for i, (name, source) in enumerate(scripts):
        items.append(_array_me(mc, [
            _int_me(mc, 10_000 + i),
            _str_me(mc, name),
            _str_me(mc, zlib.compress(source.encode("utf-8")).decode("latin1")),
        ]))
        # _str_me encodes text as UTF-8, but script payload is binary compressed
        items[-1].data[2].at().data = zlib.compress(source.encode("utf-8"))
    return _build(mc, _array_me(mc, items))


def _write_data_files(tmp: Path, files: dict[str, bytes]) -> Path:
    data = tmp / "Data"
    data.mkdir()
    for name, content in files.items():
        (data / name).write_bytes(content)
    return tmp


def test_note_extract_mode_skips_ascii_plugin_config():
    assert _note_extract_mode('<play_footsound>') == 'skip'
    assert _note_extract_mode('<ft: gold_rate 1.08>') == 'skip'
    assert _note_extract_mode('<state overlay: 4,5,6>') == 'skip'
    assert _note_extract_mode('"<play_footsound>"\n<战时装备变更>\n<禁止更换:8,3,2>') == 'skip'


def test_note_extract_mode_preserves_cjk_prose_tags():
    assert _note_extract_mode('<战斗结束时退队>') == 'whole'
    assert _note_extract_mode('说明：这里会改变角色状态。') == 'whole'


def test_ruby_string_literals_extracts_only_cjk_quoted_text():
    line = 'bar_v(23,40,100,0,"精神值变化:")'
    assert _ruby_string_literals(line) == [(0, '精神值变化:')]


def test_ruby_string_literals_handles_single_quotes_and_escapes():
    line = "show_text('她说\\'你好\\'')"
    assert _ruby_string_literals(line) == [(0, "她说'你好'")]


def test_replace_ruby_string_literal_preserves_code_shape():
    line = 'bar_v(23,40,100,0,"精神值变化:")'
    assert _replace_ruby_string_literal(line, 0, 'Thay đổi tinh thần:') == 'bar_v(23,40,100,0,"Thay đổi tinh thần:")'


def test_replace_ruby_string_literal_escapes_quote():
    line = 'msg("你好")'
    assert _replace_ruby_string_literal(line, 0, 'Anh ấy nói "chào"') == 'msg("Anh ấy nói \\"chào\\"")'


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

    # 655 script string literal
    script = by_key["$.events[1].pages[0].list[7].parameters[0].ruby_string[0]"]
    assert script.source == "怪しい影"
    assert script.context == "rpg_maker_map_script_string"
    assert script.context_text == 'p "怪しい影"'


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


def test_apply_roundtrip_changes_event_script_literal_only(tmp_path: Path):
    payload = _make_map_file([
        (355, ['bar_v(23,40,100,0,"精神值变化:")']),
    ])
    src = tmp_path / "Data" / "Map001.rvdata2"
    src.parent.mkdir()
    src.write_bytes(payload)
    entries = extract_rpg_maker_vxace(tmp_path)
    script = next(e for e in entries if e.context == "rpg_maker_map_script_string")
    assert script.key == "$.events[1].pages[0].list[0].parameters[0].ruby_string[0]"
    out = tmp_path / "out"
    apply_rpg_maker_vxace([
        TranslationResult(file=script.file, key=script.key, source=script.source, target="Thay đổi tinh thần:")
    ], out)
    mc = MC.load((out / "Map001.rvdata2").read_bytes())
    root = mc.root
    node = _resolve_path(root, _parse_path("$.events[1].pages[0].list[0].parameters[0]"))
    assert node is not None
    assert node.at().data.decode("utf-8") == 'bar_v(23,40,100,0,"Thay đổi tinh thần:")'


def test_apply_rpg_maker_vxace_empty_results_returns_summary(tmp_path: Path):
    summary = apply_rpg_maker_vxace([], tmp_path / "out")
    assert summary.applied == 0
    assert summary.skipped == 0
    assert summary.files_written == 0


def test_round_trip_byte_equal_for_unchanged_string(tmp_path: Path):
    payload = _make_actors_file([{"name": "太郎", "nickname": "勇者"}])
    # Round-trip through MC
    mc = MC.load(payload)
    redumped = mc.dump()
    assert redumped == payload


def test_extract_scripts_vocabulary_strings(tmp_path: Path):
    payload = _make_scripts_file([("Vocab", 'SaveMessage = "要暂时在哪里停止回忆呢？"\nLoadMessage = "要回忆起哪个记忆呢？"')])
    _write_data_files(tmp_path, {"Scripts.rvdata2": payload})
    entries = extract_rpg_maker_vxace(tmp_path)
    by_source = {e.source: e for e in entries}
    assert by_source["要暂时在哪里停止回忆呢？"].key == "$[0].source.ruby_string[0]"
    assert by_source["要回忆起哪个记忆呢？"].context == "rpg_maker_vxace_script_vocab_string"
    assert by_source["要回忆起哪个记忆呢？"].context_text == "Vocab"


def test_apply_scripts_vocabulary_string_roundtrip(tmp_path: Path):
    payload = _make_scripts_file([("Vocab", 'SaveMessage = "要暂时在哪里停止回忆呢？"')])
    _write_data_files(tmp_path, {"Scripts.rvdata2": payload})
    entry = extract_rpg_maker_vxace(tmp_path)[0]
    out = tmp_path / "out"
    apply_rpg_maker_vxace([TranslationResult(entry.file, entry.key, entry.source, "Tạm dừng hồi ức ở đâu?")], out)
    mc = MC.load((out / "Scripts.rvdata2").read_bytes())
    _script_id, _name, compressed = _script_record_parts(mc.root.at().data[0])
    assert compressed is not None
    assert "Tạm dừng hồi ức ở đâu?" in _decode_script_source(compressed)


def test_extract_skips_invalid_scripts_rvdata2(tmp_path: Path):
    fake = b"\x04\x08\"\x06A"
    _write_data_files(tmp_path, {"Scripts.rvdata2": fake})
    entries = extract_rpg_maker_vxace(tmp_path)
    assert entries == []
