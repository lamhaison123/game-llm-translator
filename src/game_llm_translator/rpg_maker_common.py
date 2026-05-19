from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .models import TextEntry, TranslationResult

RPG_MAKER_TEXT_KEYS = {"name", "nickname", "profile", "description", "message", "displayName"}
RPG_MAKER_DATABASE_TEXT_FIELDS = {
    "Actors.json": {"name", "nickname", "profile"},
    "Classes.json": {"name"},
    "Skills.json": {"name", "description", "message1", "message2"},
    "Items.json": {"name", "description"},
    "Weapons.json": {"name", "description"},
    "Armors.json": {"name", "description"},
    "Enemies.json": {"name"},
    "States.json": {"name", "message1", "message2", "message3", "message4"},
}
RPG_MAKER_SYSTEM_TEXT_KEYS = {"gameTitle", "currencyUnit"}
RPG_MAKER_SYSTEM_ARRAY_KEYS = {"armorTypes", "elements", "equipTypes", "skillTypes", "weaponTypes"}
RPG_MAKER_SYSTEM_TERM_KEYS = {"basic", "commands", "params", "messages"}
RPG_MAKER_ASSET_NAME_KEYS = {
    "animation1Name",
    "animation2Name",
    "battleback1Name",
    "battleback2Name",
    "battlerName",
    "characterName",
    "faceName",
    "parallaxName",
    "shipName",
    "boatName",
    "airshipName",
    "title1Name",
    "title2Name",
}
RPG_MAKER_AUDIO_KEYS = {"bgm", "bgs", "me", "se", "battleBgm", "titleBgm", "victoryMe", "defeatMe"}
RPG_MAKER_EVENT_TEXT_CODES = {401, 405}
RPG_MAKER_CHOICE_CODE = 102
RPG_MAKER_SKIP_DIRS = {"新しいフォルダー", "新しいフォルダー - コピー", "backup", "backups"}
RPG_MAKER_JSON_ENGINES = {"mv", "mz", "mv-mz"}


def is_supported_json_engine(engine: str | None) -> bool:
    return engine in RPG_MAKER_JSON_ENGINES


def normalize_gui_game_type(value: str | None) -> str:
    if value in {"rpg-maker-mz", "mz"}:
        return "rpg-maker-mz"
    return "rpg-maker-mv"


def engine_to_gui_game_type(engine: str | None) -> str:
    if engine == "mz":
        return "rpg-maker-mz"
    return "rpg-maker-mv"


def gui_game_type_to_engine(value: str | None) -> str:
    return "mz" if normalize_gui_game_type(value) == "rpg-maker-mz" else "mv"


def _is_text(value: Any) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _is_event_text_command(value: dict[str, Any]) -> bool:
    params = value.get("parameters")
    return value.get("code") in RPG_MAKER_EVENT_TEXT_CODES and isinstance(params, list) and bool(params) and _is_text(params[0])


def _is_choice_command(value: dict[str, Any]) -> bool:
    params = value.get("parameters")
    return value.get("code") == RPG_MAKER_CHOICE_CODE and isinstance(params, list) and bool(params) and isinstance(params[0], list)


def _is_translatable_array(key: str, value: Any) -> bool:
    return key in RPG_MAKER_SYSTEM_ARRAY_KEYS and isinstance(value, list)


def _detect_mv_mz(game_dir: Path) -> str | None:
    if (game_dir / "www" / "js" / "rmmz_core.js").exists() or (game_dir / "js" / "rmmz_core.js").exists():
        return "mz"
    if (game_dir / "www" / "js" / "rpg_core.js").exists() or (game_dir / "js" / "rpg_core.js").exists():
        return "mv"
    if (game_dir / "www" / "package.json").exists() and (game_dir / "www" / "data").exists():
        return "mv"
    if (game_dir / "www" / "data").exists() or (game_dir / "data").exists():
        return "mv-mz"
    return None


def detect_rpg_maker(game_dir: Path) -> str | None:
    engine = _detect_mv_mz(game_dir)
    if engine:
        return engine
    ini_path = game_dir / "Game.ini"
    if ini_path.exists():
        try:
            ini_text = ini_path.read_text(encoding="utf-8-sig", errors="ignore")
        except Exception:
            ini_text = ""
        if "RGSS3" in ini_text:
            return "vx-ace"
        if "RGSS2" in ini_text:
            return "vx"
        if "RGSS1" in ini_text:
            return "xp"
    if any((game_dir / "Data").glob("*.rvdata2")):
        return "vx-ace"
    if any((game_dir / "Data").glob("*.rvdata")):
        return "vx"
    if any((game_dir / "Data").glob("*.rxdata")):
        return "xp"
    return None


def _event_context_text(value: dict[str, Any]) -> str:
    commands = value.get("list")
    if not isinstance(commands, list):
        return ""
    lines: list[str] = []
    for command in commands:
        if not isinstance(command, dict) or not _is_event_text_command(command):
            continue
        text = command["parameters"][0]
        if text not in lines:
            lines.append(text)
    return "\n".join(lines[:12])


def _looks_like_audio_object(value: Any) -> bool:
    return isinstance(value, dict) and isinstance(value.get("name"), str) and any(key in value for key in ("volume", "pitch", "pan"))


def _is_event_object(value: dict[str, Any]) -> bool:
    return isinstance(value.get("id"), int) and isinstance(value.get("pages"), list) and ("x" in value or "y" in value or "list" in value)


def _append_text_entry(entries: list[TextEntry], file: Path, key: str, source: Any, context: str, context_text: str = "") -> None:
    if _is_text(source):
        entries.append(TextEntry(file=file, key=key, source=source, context=context, context_text=context_text))


def _walk_terms(value: Any, file: Path, prefix: str, context: str = "rpg_maker_terms") -> list[TextEntry]:
    entries: list[TextEntry] = []
    if isinstance(value, dict):
        for key, child in value.items():
            child_key = f"{prefix}.{key}"
            child_context = f"{context}_{key}"
            if isinstance(child, str):
                _append_text_entry(entries, file, child_key, child, child_context)
            elif isinstance(child, list):
                for index, item in enumerate(child):
                    _append_text_entry(entries, file, f"{child_key}[{index}]", item, child_context)
            elif isinstance(child, dict):
                entries.extend(_walk_terms(child, file, child_key, child_context))
    elif isinstance(value, list):
        for index, item in enumerate(value):
            _append_text_entry(entries, file, f"{prefix}[{index}]", item, context)
    return entries


def _walk_system_json(value: Any, file: Path, prefix: str = "$") -> list[TextEntry]:
    entries: list[TextEntry] = []
    if not isinstance(value, dict):
        return entries
    for key, child in value.items():
        child_key = f"{prefix}.{key}"
        if key in RPG_MAKER_SYSTEM_TEXT_KEYS:
            _append_text_entry(entries, file, child_key, child, f"rpg_maker_system_{key}")
        elif key in RPG_MAKER_SYSTEM_ARRAY_KEYS and isinstance(child, list):
            for index, item in enumerate(child):
                _append_text_entry(entries, file, f"{child_key}[{index}]", item, f"rpg_maker_{key}")
        elif key == "terms" and isinstance(child, dict):
            for term_key in RPG_MAKER_SYSTEM_TERM_KEYS:
                if term_key in child:
                    entries.extend(_walk_terms(child[term_key], file, f"{child_key}.{term_key}", f"rpg_maker_terms_{term_key}"))
    return entries


def _walk_database_json(value: Any, file: Path, prefix: str = "$") -> list[TextEntry]:
    entries: list[TextEntry] = []
    fields = RPG_MAKER_DATABASE_TEXT_FIELDS.get(file.name, set())
    if isinstance(value, list):
        for index, item in enumerate(value):
            if not isinstance(item, dict):
                continue
            for field in fields:
                _append_text_entry(entries, file, f"{prefix}[{index}].{field}", item.get(field), f"rpg_maker_{file.stem.lower()}_{field}")
    elif isinstance(value, dict):
        for field in fields:
            _append_text_entry(entries, file, f"{prefix}.{field}", value.get(field), f"rpg_maker_{file.stem.lower()}_{field}")
    return entries


def _walk_event_json(value: Any, file: Path, prefix: str = "$", inherited_context: str = "", plugin_text_extractor=None) -> list[TextEntry]:
    entries: list[TextEntry] = []
    if isinstance(value, dict):
        local_context = _event_context_text(value) or inherited_context
        if _is_event_object(value):
            local_context = inherited_context
        if _is_event_text_command(value):
            entries.append(TextEntry(file=file, key=f"{prefix}.parameters[0]", source=value["parameters"][0], context="rpg_maker_event_text", context_text=inherited_context))
            return entries
        if _is_choice_command(value):
            for index, choice in enumerate(value["parameters"][0]):
                if _is_text(choice):
                    entries.append(TextEntry(file=file, key=f"{prefix}.parameters[0][{index}]", source=choice, context="rpg_maker_choice", context_text=local_context))
            return entries
        if plugin_text_extractor is not None:
            plugin_entries = plugin_text_extractor(value, file, prefix, local_context)
            if plugin_entries:
                entries.extend(plugin_entries)
                return entries
        for key, child in value.items():
            child_key = f"{prefix}.{key}"
            if key == "displayName":
                _append_text_entry(entries, file, child_key, child, "rpg_maker_map_display_name", local_context)
            else:
                entries.extend(_walk_event_json(child, file, child_key, local_context, plugin_text_extractor))
    elif isinstance(value, list):
        for index, child in enumerate(value):
            entries.extend(_walk_event_json(child, file, f"{prefix}[{index}]", inherited_context, plugin_text_extractor))
    return entries


def _walk_json(value: Any, file: Path, prefix: str = "$", inherited_context: str = "", plugin_text_extractor=None) -> list[TextEntry]:
    if file.name == "System.json":
        return _walk_system_json(value, file, prefix)
    if file.name in RPG_MAKER_DATABASE_TEXT_FIELDS:
        return _walk_database_json(value, file, prefix)
    if file.name == "CommonEvents.json" or file.name == "Troops.json" or file.name.startswith("Map"):
        return _walk_event_json(value, file, prefix, inherited_context, plugin_text_extractor)
    return []


def _data_dir(game_dir: Path) -> Path:
    data_dir = game_dir / "www" / "data"
    if data_dir.exists():
        return data_dir
    return game_dir / "data"


def extract_rpg_maker_json(game_dir: Path, plugin_text_extractor=None) -> list[TextEntry]:
    data_dir = _data_dir(game_dir)
    entries: list[TextEntry] = []
    for file in data_dir.glob("*.json"):
        if any(part in RPG_MAKER_SKIP_DIRS for part in file.relative_to(data_dir).parts[:-1]):
            continue
        try:
            data = json.loads(file.read_text(encoding="utf-8-sig"))
        except Exception:
            continue
        entries.extend(_walk_json(data, file, plugin_text_extractor=plugin_text_extractor))
    return entries


def _parse_path(path: str) -> list[str | int]:
    parts: list[str | int] = []
    token = ""
    i = 1
    while i < len(path):
        char = path[i]
        if char == ".":
            if token:
                parts.append(token)
                token = ""
        elif char == "[":
            if token:
                parts.append(token)
                token = ""
            end = path.index("]", i)
            parts.append(int(path[i + 1:end]))
            i = end
        else:
            token += char
        i += 1
    if token:
        parts.append(token)
    return parts


def _set_json_value(data: Any, path: str, value: str) -> None:
    ref = data
    parts = _parse_path(path)
    for part in parts[:-1]:
        ref = ref[part]
    ref[parts[-1]] = value


def apply_rpg_maker(results: list[TranslationResult], output_dir: Path) -> None:
    grouped: dict[Path, list[TranslationResult]] = {}
    for result in results:
        grouped.setdefault(result.file, []).append(result)
    output_dir.mkdir(parents=True, exist_ok=True)
    for file, file_results in grouped.items():
        data = json.loads(file.read_text(encoding="utf-8-sig"))
        for result in file_results:
            _set_json_value(data, result.key, result.target)
        target = output_dir / file.name
        target.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
