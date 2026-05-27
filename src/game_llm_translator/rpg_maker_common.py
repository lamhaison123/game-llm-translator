from __future__ import annotations

import json
import re as _re
from pathlib import Path
from typing import Any

from .app_logging import log_event
from .models import TextEntry, TranslationResult

RPG_MAKER_TEXT_KEYS = {"name", "nickname", "profile", "description", "message", "displayName"}
RPG_MAKER_DATABASE_TEXT_FIELDS = {
    "Actors.json": {"name", "nickname", "profile", "note"},
    "Classes.json": {"name", "note"},
    "Skills.json": {"name", "description", "message1", "message2", "note"},
    "Items.json": {"name", "description", "note"},
    "Weapons.json": {"name", "description", "note"},
    "Armors.json": {"name", "description", "note"},
    "Enemies.json": {"name", "note"},
    "States.json": {"name", "description", "message1", "message2", "message3", "message4", "note"},
    "Animations.json": {"name"},
    "Tilesets.json": {"name", "note"},
}
RPG_MAKER_SYSTEM_TEXT_KEYS = {"gameTitle", "currencyUnit"}
RPG_MAKER_SYSTEM_ARRAY_KEYS = {"armorTypes", "elements", "equipTypes", "skillTypes", "weaponTypes", "switches", "variables"}
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
RPG_MAKER_COMMENT_CODES = {108, 408}
RPG_MAKER_SCRIPT_CODES = {355, 655}
RPG_MAKER_PLUGIN_COMMAND_MV = 356
RPG_MAKER_PLUGIN_COMMAND_MZ = 357
RPG_MAKER_DIALOGUE_BLOCK_START = 101
RPG_MAKER_DIALOGUE_TEXT_LINE = 401
RPG_MAKER_CHOICE_CODE = 102
RPG_MAKER_CHANGE_NAME_CODE = 320
RPG_MAKER_CHANGE_NICKNAME_CODE = 324
RPG_MAKER_CHANGE_PROFILE_CODE = 325
RPG_MAKER_SKIP_DIRS = {"新しいフォルダー", "新しいフォルダー - コピー", "backup", "backups"}
RPG_MAKER_JSON_ENGINES = {"mv", "mz", "mv-mz"}

RPG_MAKER_COMMAND_LABELS: dict[int, str] = {
    101: "dialogue_show_text",
    102: "dialogue_show_choices",
    103: "dialogue_input_number",
    104: "dialogue_select_item",
    105: "dialogue_scroll_text",
    108: "comment",
    111: "conditional_branch",
    117: "call_common_event",
    121: "switch_operation",
    122: "variable_operation",
    123: "self_switch_operation",
    125: "change_gold",
    126: "change_item",
    127: "change_weapon",
    128: "change_armor",
    129: "change_party_member",
    201: "transfer_player",
    205: "set_move_route",
    211: "set_transparency",
    212: "show_animation",
    213: "show_balloon_icon",
    214: "erase_event",
    221: "fadeout_screen",
    222: "fadein_screen",
    223: "tint_screen",
    224: "flash_screen",
    225: "shake_screen",
    230: "wait",
    231: "show_picture",
    232: "move_picture",
    235: "erase_picture",
    241: "play_bgm",
    245: "play_se",
    250: "plugin_command_mv",
    281: "change_map_display_name",
    301: "battle_processing",
    302: "shop_processing",
    303: "name_input_processing",
    311: "change_hp",
    312: "change_mp",
    313: "change_state",
    314: "full_recovery",
    315: "change_exp",
    316: "change_level",
    317: "change_parameter",
    318: "change_skill",
    319: "change_equipment",
    320: "change_actor_name",
    321: "change_actor_class",
    322: "change_actor_graphic",
    324: "change_actor_nickname",
    325: "change_actor_profile",
    331: "change_enemy_hp",
    337: "show_battle_animation",
    339: "force_action",
    355: "script_call",
    357: "plugin_command_mz",
    401: "dialogue_text_line",
    402: "dialogue_choice_option",
    403: "dialogue_choice_cancel",
    404: "dialogue_choice_end",
    405: "dialogue_scroll_text_line",
    408: "comment_line",
    411: "else_branch",
    412: "branch_end",
    413: "loop_above",
    505: "move_route_step",
    601: "battle_victory",
    602: "battle_escape",
    603: "battle_defeat",
    604: "battle_end",
    655: "script_line",
    657: "mz_script_label",
}


def event_command_label(code: int) -> str:
    return RPG_MAKER_COMMAND_LABELS.get(code, f"command_{code}")


def is_supported_json_engine(engine: str | None) -> bool:
    return engine in RPG_MAKER_JSON_ENGINES


def normalize_gui_game_type(value: str | None) -> str:
    if value in {"unity-xunity", "xunity"}:
        return "unity-xunity"
    if value in {"rpg-maker-mz", "mz"}:
        return "rpg-maker-mz"
    return "rpg-maker-mv"


def engine_to_gui_game_type(engine: str | None) -> str:
    if engine == "unity-xunity":
        return "unity-xunity"
    if engine in {"mz", "mv-mz"}:
        return "rpg-maker-mz"
    return "rpg-maker-mv"


def gui_game_type_to_engine(value: str | None) -> str:
    normalized = normalize_gui_game_type(value)
    if normalized == "unity-xunity":
        return "unity-xunity"
    if normalized == "rpg-maker-mz":
        return "mz"
    if normalized == "rpg-maker-mv":
        return "mv"
    # fallback
    return "mv"


_ONLY_CONTROL_CODE_RE = _re.compile(
    r'^(?:[\s\\]*(?:\\F[A-Za-z]*\[[^\]]*\]|\\OC\[\d+\]|\\OO\[\d+\]|\\FS\[\d+\]|\\[A-Za-z]+\[[^\]]*\]|\\[{}.$!><^_\\]|%\d+))*[\s\\]*$'
)


def _is_text(value: Any) -> bool:
    if not isinstance(value, str) or not value.strip():
        return False
    if _ONLY_CONTROL_CODE_RE.match(value.strip()):
        return False
    return True


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


def _event_context_text(value: dict[str, Any], surrounding_commands: list[dict[str, Any]] | None = None) -> str:
    commands = value.get("list")
    if not isinstance(commands, list):
        commands = surrounding_commands if surrounding_commands else None
        if not commands:
            return ""
    parts: list[str] = []
    current_speaker = ""
    for command in commands:
        if not isinstance(command, dict):
            continue
        code = command.get("code")
        params = command.get("parameters")
        if code == 101 and isinstance(params, list) and len(params) >= 5:
            speaker = str(params[4]) if params[4] else ""
            if speaker:
                current_speaker = speaker
                if f"[{speaker}]" not in parts:
                    parts.append(f"[{speaker}]")
        if code == RPG_MAKER_CHOICE_CODE and isinstance(params, list) and params and isinstance(params[0], list):
            for choice in params[0]:
                if isinstance(choice, str) and choice.strip() and choice not in parts:
                    parts.append(choice)
        if code in RPG_MAKER_EVENT_TEXT_CODES and isinstance(params, list) and params and _is_text(params[0]):
            text = params[0]
            if current_speaker:
                tagged = f"[{current_speaker}] {text}"
                if tagged not in parts:
                    parts.append(tagged)
            elif text not in parts:
                parts.append(text)
        if len(parts) >= 16:
            break
    return "\n".join(parts)


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


_PLUGIN_UI_TEXT_KEYS = {"text", "label", "title", "description", "placeholder", "tooltip"}
_PLUGIN_UI_SKIP_KEYS = {
    "type", "id", "folderName", "imageName", "faceName", "faceIndex",
    "fillColor", "strokeColor", "textColor", "borderColor", "shadow",
    "outline", "font", "alignment", "verticalCentered", "multiline",
    "fontSize", "corners", "strokeWidth", "fillAlpha", "borderThickness",
    "borderOpacity", "backgroundType", "itemsPadding", "maxCols",
    "onPurchaseSE", "onMessageAddedSE", "messageBaseHeight",
    "messageExtraLineHeightAdd", "playScaleAnimation", "initialScale",
    "finalScale", "scaleChangeStep", "clickAnimation",
    "showAppContentDelayMS", "scrollVerticalStep",
    "nextMessageWaitTimeInSeconds",
}


def _is_translatable_ui_text(value: Any) -> bool:
    if not isinstance(value, str) or not value.strip():
        return False
    if len(value.strip()) < 2:
        return False
    if value.strip().startswith("$") or value.strip().startswith("@"):
        return False
    if value.strip().startswith("#") and len(value.strip()) in (4, 7):
        return False
    if value.strip() in {"true", "false", "center", "left", "right", "top", "bottom"}:
        return False
    return True


def _walk_plugin_ui_json(value: Any, file: Path, prefix: str = "$") -> list[TextEntry]:
    """Walk custom plugin UI JSON (PKD_PhoneMenu, etc.) extracting translatable text."""
    entries: list[TextEntry] = []
    if isinstance(value, dict):
        for key, child in value.items():
            child_key = f"{prefix}.{key}"
            if key in _PLUGIN_UI_SKIP_KEYS:
                continue
            if key in _PLUGIN_UI_TEXT_KEYS and _is_translatable_ui_text(child):
                rel = file.relative_to(file.parents[1]) if len(file.parents) > 1 else file.name
                context = f"rpg_maker_plugin_ui_{rel.parent.name if rel.parent.name != '.' else 'custom'}"
                entries.append(TextEntry(file=file, key=child_key, source=child, context=context))
            if key == "text" and isinstance(child, list):
                for i, item in enumerate(child):
                    if _is_translatable_ui_text(item):
                        rel = file.relative_to(file.parents[1]) if len(file.parents) > 1 else file.name
                        context = f"rpg_maker_plugin_ui_{rel.parent.name if rel.parent.name != '.' else 'custom'}"
                        entries.append(TextEntry(file=file, key=f"{child_key}[{i}]", source=item, context=context))
            elif isinstance(child, (dict, list)):
                entries.extend(_walk_plugin_ui_json(child, file, child_key))
    elif isinstance(value, list):
        for index, child in enumerate(value):
            if isinstance(child, str) and _is_translatable_ui_text(child):
                entries.append(TextEntry(file=file, key=f"{prefix}[{index}]", source=child, context="rpg_maker_plugin_ui_custom"))
            elif isinstance(child, (dict, list)):
                entries.extend(_walk_plugin_ui_json(child, file, f"{prefix}[{index}]"))
    return entries


def _walk_generic_text(value: Any, file: Path, prefix: str = "$") -> list[TextEntry]:
    """Greedy walker for unknown JSON files (PKD_*, Windows.json, etc.).

    Extracts every string passing _is_translatable_ui_text, mirroring
    Translator++'s rmmvjs behavior. Skips known noise keys.
    """
    entries: list[TextEntry] = []
    context = f"rpg_maker_generic_{file.stem.lower()}"
    if isinstance(value, dict):
        for key, child in value.items():
            child_key = f"{prefix}.{key}"
            if key in _PLUGIN_UI_SKIP_KEYS:
                continue
            if isinstance(child, str):
                if _is_translatable_ui_text(child):
                    entries.append(TextEntry(file=file, key=child_key, source=child, context=context))
            elif isinstance(child, (dict, list)):
                entries.extend(_walk_generic_text(child, file, child_key))
    elif isinstance(value, list):
        for index, child in enumerate(value):
            child_key = f"{prefix}[{index}]"
            if isinstance(child, str):
                if _is_translatable_ui_text(child):
                    entries.append(TextEntry(file=file, key=child_key, source=child, context=context))
            elif isinstance(child, (dict, list)):
                entries.extend(_walk_generic_text(child, file, child_key))
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


def _walk_map_infos_json(value: Any, file: Path, prefix: str = "$") -> list[TextEntry]:
    """MapInfos.json: player-visible map names (save screen, current location UI)."""
    entries: list[TextEntry] = []
    if isinstance(value, list):
        for index, item in enumerate(value):
            if isinstance(item, dict):
                _append_text_entry(entries, file, f"{prefix}[{index}].name", item.get("name"), "rpg_maker_map_name")
    return entries


def _walk_troops_top_level_names(value: Any, file: Path, prefix: str = "$") -> list[TextEntry]:
    """Troops.json: top-level troop names (shown in battle encounter UI).

    The recursive event walker handles each troop's pages.list event commands,
    but skips the troop object's top-level `name` field. This extracts those.
    """
    entries: list[TextEntry] = []
    if isinstance(value, list):
        for index, item in enumerate(value):
            if isinstance(item, dict) and isinstance(item.get("name"), str):
                _append_text_entry(entries, file, f"{prefix}[{index}].name", item.get("name"), "rpg_maker_troops_name")
    return entries


def _merge_dialogue_blocks(
    commands: list[Any],
    file: Path,
    prefix: str,
    local_context: str,
    plugin_text_extractor,
) -> list[TextEntry]:
    """Walk an event command list, merging consecutive 401 text lines into dialogue blocks.

    Inspired by RPGMTL: multi-line dialogue (code 101 followed by consecutive 401 lines)
    is merged into a single TextEntry so the LLM sees the complete sentence/paragraph.
    Uses newline as line separator; sub_keys tracks individual keys for patching.
    """
    entries: list[TextEntry] = []
    i = 0
    while i < len(commands):
        cmd = commands[i]
        if not isinstance(cmd, dict):
            i += 1
            continue
        code = cmd.get("code")
        params = cmd.get("parameters")

        if code == RPG_MAKER_DIALOGUE_BLOCK_START and isinstance(params, list):
            cmd_prefix = f"{prefix}[{i}]"
            speaker_name = ""
            if len(params) >= 5 and params[4] and _is_text(str(params[4])):
                speaker_name = str(params[4])
                speaker_context = f"[{speaker_name}]"
                preceding_text = local_context
                context_for_speaker = f"{speaker_context}\n{preceding_text}" if preceding_text else speaker_context
                entries.append(TextEntry(
                    file=file,
                    key=f"{cmd_prefix}.parameters[4]",
                    source=speaker_name,
                    context="rpg_maker_speaker_name",
                    context_text=context_for_speaker,
                ))

            text_lines: list[str] = []
            text_keys: list[str] = []
            j = i + 1
            while j < len(commands):
                next_cmd = commands[j]
                if not isinstance(next_cmd, dict):
                    j += 1
                    continue
                if next_cmd.get("code") not in RPG_MAKER_EVENT_TEXT_CODES:
                    break
                next_params = next_cmd.get("parameters")
                if not isinstance(next_params, list) or not next_params or not _is_text(next_params[0]):
                    break
                text_lines.append(next_params[0])
                text_keys.append(f"{prefix}[{j}].parameters[0]")
                j += 1

            if text_lines:
                merged_text = "\n".join(text_lines)
                block_context = local_context
                if speaker_name:
                    block_context = f"[{speaker_name}]\n{local_context}" if local_context else f"[{speaker_name}]"
                if len(text_lines) > 1:
                    entries.append(TextEntry(
                        file=file,
                        key=text_keys[0],
                        source=merged_text,
                        context="rpg_maker_event_text",
                        context_text=block_context,
                        sub_keys=text_keys,
                    ))
                else:
                    entries.append(TextEntry(
                        file=file,
                        key=text_keys[0],
                        source=text_lines[0],
                        context="rpg_maker_event_text",
                        context_text=block_context,
                    ))
            i = j
            continue

        if code in RPG_MAKER_EVENT_TEXT_CODES and code != RPG_MAKER_DIALOGUE_BLOCK_START:
            if isinstance(params, list) and params and _is_text(params[0]):
                entries.append(TextEntry(
                    file=file,
                    key=f"{prefix}[{i}].parameters[0]",
                    source=params[0],
                    context="rpg_maker_event_text",
                    context_text=local_context,
                ))
            i += 1
            continue

        if code in RPG_MAKER_COMMENT_CODES:
            text_lines: list[str] = []
            text_keys: list[str] = []
            j = i
            while j < len(commands):
                next_cmd = commands[j]
                if not isinstance(next_cmd, dict) or next_cmd.get("code") not in RPG_MAKER_COMMENT_CODES:
                    break
                next_params = next_cmd.get("parameters")
                if not isinstance(next_params, list) or not next_params or not _is_text(next_params[0]):
                    j += 1
                    continue
                text_lines.append(next_params[0])
                text_keys.append(f"{prefix}[{j}].parameters[0]")
                j += 1
            if text_lines:
                merged = "\n".join(text_lines)
                if len(text_lines) > 1:
                    entries.append(TextEntry(file=file, key=text_keys[0], source=merged, context="rpg_maker_comment", context_text=local_context, sub_keys=text_keys))
                else:
                    entries.append(TextEntry(file=file, key=text_keys[0], source=text_lines[0], context="rpg_maker_comment", context_text=local_context))
            i = max(j, i + 1)
            continue

        if code in RPG_MAKER_SCRIPT_CODES:
            text_lines: list[str] = []
            text_keys: list[str] = []
            j = i
            while j < len(commands):
                next_cmd = commands[j]
                if not isinstance(next_cmd, dict) or next_cmd.get("code") not in RPG_MAKER_SCRIPT_CODES:
                    break
                next_params = next_cmd.get("parameters")
                if not isinstance(next_params, list) or not next_params or not _is_text(next_params[0]):
                    j += 1
                    continue
                text_lines.append(next_params[0])
                text_keys.append(f"{prefix}[{j}].parameters[0]")
                j += 1
            if text_lines:
                merged = "\n".join(text_lines)
                if len(text_lines) > 1:
                    entries.append(TextEntry(file=file, key=text_keys[0], source=merged, context="rpg_maker_script", context_text=local_context, sub_keys=text_keys))
                else:
                    entries.append(TextEntry(file=file, key=text_keys[0], source=text_lines[0], context="rpg_maker_script", context_text=local_context))
            i = max(j, i + 1)
            continue

        if code == RPG_MAKER_PLUGIN_COMMAND_MV and isinstance(params, list) and params and _is_text(params[0]):
            entries.append(TextEntry(
                file=file,
                key=f"{prefix}[{i}].parameters[0]",
                source=params[0],
                context="rpg_maker_plugin_command",
                context_text=local_context,
            ))
            i += 1
            continue

        if code == RPG_MAKER_PLUGIN_COMMAND_MZ and isinstance(params, list):
            for p_idx, pval in enumerate(params):
                if _is_text(pval):
                    entries.append(TextEntry(
                        file=file,
                        key=f"{prefix}[{i}].parameters[{p_idx}]",
                        source=pval,
                        context="rpg_maker_plugin_command",
                        context_text=local_context,
                    ))
            i += 1
            continue

        if _is_choice_command(cmd):
            if isinstance(params, list) and params and isinstance(params[0], list):
                for index, choice in enumerate(params[0]):
                    if _is_text(choice):
                        entries.append(TextEntry(
                            file=file,
                            key=f"{prefix}[{i}].parameters[0][{index}]",
                            source=choice,
                            context="rpg_maker_choice",
                            context_text=local_context,
                        ))
            i += 1
            continue

        if code == RPG_MAKER_CHANGE_NAME_CODE and isinstance(params, list) and len(params) >= 2 and _is_text(params[1]):
            entries.append(TextEntry(file=file, key=f"{prefix}[{i}].parameters[1]", source=params[1], context="rpg_maker_actors_name", context_text=local_context))
            i += 1
            continue

        if code == RPG_MAKER_CHANGE_NICKNAME_CODE and isinstance(params, list) and len(params) >= 2 and _is_text(params[1]):
            entries.append(TextEntry(file=file, key=f"{prefix}[{i}].parameters[1]", source=params[1], context="rpg_maker_actors_nickname", context_text=local_context))
            i += 1
            continue

        if code == RPG_MAKER_CHANGE_PROFILE_CODE and isinstance(params, list) and len(params) >= 2 and _is_text(params[1]):
            entries.append(TextEntry(file=file, key=f"{prefix}[{i}].parameters[1]", source=params[1], context="rpg_maker_actors_profile", context_text=local_context))
            i += 1
            continue

        if plugin_text_extractor is not None:
            plugin_entries = plugin_text_extractor(cmd, file, f"{prefix}[{i}]", local_context)
            if plugin_entries:
                entries.extend(plugin_entries)
                i += 1
                continue

        i += 1

    return entries


def _walk_event_json(value: Any, file: Path, prefix: str = "$", inherited_context: str = "", plugin_text_extractor=None) -> list[TextEntry]:
    entries: list[TextEntry] = []
    if isinstance(value, dict):
        local_context = _event_context_text(value) or inherited_context
        if _is_event_object(value):
            local_context = inherited_context
        code = value.get("code")
        params = value.get("parameters")
        if _is_event_text_command(value):
            entries.append(TextEntry(file=file, key=f"{prefix}.parameters[0]", source=value["parameters"][0], context="rpg_maker_event_text", context_text=local_context))
        if code == 101 and isinstance(params, list) and len(params) >= 5 and _is_text(params[4]):
            # Speaker name is extracted by _merge_dialogue_blocks when this command
            # is inside a list. Only extract here if encountered outside a list
            # (unusual but possible in custom game data).
            if not any(isinstance(child, list) and any(isinstance(item, dict) and item.get("code") == 101 for item in child) for child in value.values() if isinstance(child, list)):
                speaker_name = str(params[4])
                speaker_context = f"[{speaker_name}]" if speaker_name else ""
                preceding_text = local_context
                if preceding_text:
                    context_for_speaker = f"{speaker_context}\n{preceding_text}"
                else:
                    context_for_speaker = speaker_context
                entries.append(TextEntry(file=file, key=f"{prefix}.parameters[4]", source=params[4], context="rpg_maker_speaker_name", context_text=context_for_speaker))
        if _is_event_text_command(value) or (code == 101 and isinstance(params, list) and len(params) >= 5 and _is_text(params[4])):
            return entries
        if _is_choice_command(value):
            for index, choice in enumerate(value["parameters"][0]):
                if _is_text(choice):
                    entries.append(TextEntry(file=file, key=f"{prefix}.parameters[0][{index}]", source=choice, context="rpg_maker_choice", context_text=local_context))
            return entries
        if code == RPG_MAKER_CHANGE_NAME_CODE and isinstance(params, list) and len(params) >= 2 and _is_text(params[1]):
            entries.append(TextEntry(file=file, key=f"{prefix}.parameters[1]", source=params[1], context="rpg_maker_actors_name", context_text=local_context))
            return entries
        if code == RPG_MAKER_CHANGE_NICKNAME_CODE and isinstance(params, list) and len(params) >= 2 and _is_text(params[1]):
            entries.append(TextEntry(file=file, key=f"{prefix}.parameters[1]", source=params[1], context="rpg_maker_actors_nickname", context_text=local_context))
            return entries
        if code == RPG_MAKER_CHANGE_PROFILE_CODE and isinstance(params, list) and len(params) >= 2 and _is_text(params[1]):
            entries.append(TextEntry(file=file, key=f"{prefix}.parameters[1]", source=params[1], context="rpg_maker_actors_profile", context_text=local_context))
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
            elif key == "name" and isinstance(value.get("id"), int) and (_is_event_object(value) or isinstance(value.get("list"), list)):
                _append_text_entry(entries, file, child_key, child, "rpg_maker_event_name", local_context)
            elif key == "note" and isinstance(value, dict) and (_is_event_object(value) or "events" in value or "data" in value):
                _append_text_entry(entries, file, child_key, child, "rpg_maker_note", local_context)
            elif key == "list" and isinstance(child, list):
                list_context = inherited_context
                parent_obj = value
                parent_name = parent_obj.get("name", "")
                parent_note = parent_obj.get("note", "")
                if parent_name or parent_note:
                    list_parts = []
                    if parent_name:
                        list_parts.append(f"[Event: {parent_name}]")
                    if parent_note:
                        list_parts.append(f"Note: {parent_note}")
                    list_context = "\n".join(list_parts)
                page_cond = parent_obj.get("conditions", {}) if isinstance(parent_obj, dict) else {}
                if isinstance(page_cond, dict):
                    actor_id = page_cond.get("actorId", 0)
                    if actor_id and isinstance(actor_id, int) and actor_id > 0:
                        list_context = f"Actor {actor_id}\n{list_context}" if list_context else f"Actor {actor_id}"
                page_context = _event_context_text({"list": child}) if isinstance(child, list) else ""
                if page_context:
                    if list_context:
                        list_context = f"{list_context}\n{page_context}"
                    else:
                        list_context = page_context
                entries.extend(_merge_dialogue_blocks(child, file, child_key, list_context, plugin_text_extractor))
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
    if file.name == "MapInfos.json":
        return _walk_map_infos_json(value, file, prefix)
    if file.name == "Troops.json":
        # Troop top-level names + recursive event walk for pages.list dialogue
        entries = _walk_troops_top_level_names(value, file, prefix)
        entries.extend(_walk_event_json(value, file, prefix, inherited_context, plugin_text_extractor))
        return entries
    if file.name == "CommonEvents.json" or file.name.startswith("Map"):
        return _walk_event_json(value, file, prefix, inherited_context, plugin_text_extractor)
    return _walk_generic_text(value, file, prefix)


def _data_dir(game_dir: Path) -> Path:
    data_dir = game_dir / "www" / "data"
    if data_dir.exists():
        return data_dir
    return game_dir / "data"


def extract_rpg_maker_json(game_dir: Path, plugin_text_extractor=None) -> tuple[list[TextEntry], list[str]]:
    data_dir = _data_dir(game_dir)
    entries: list[TextEntry] = []
    warnings: list[str] = []
    for file in data_dir.glob("*.json"):
        if any(part in RPG_MAKER_SKIP_DIRS for part in file.relative_to(data_dir).parts[:-1]):
            continue
        try:
            data = json.loads(file.read_text(encoding="utf-8-sig"))
        except json.JSONDecodeError as exc:
            msg = f"SKIP {file.name}: invalid JSON ({exc})"
            warnings.append(msg)
            log_event(msg, level="WARN")
            continue
        except OSError as exc:
            msg = f"SKIP {file.name}: read error ({exc})"
            warnings.append(msg)
            log_event(msg, level="WARN")
            continue
        entries.extend(_walk_json(data, file, plugin_text_extractor=plugin_text_extractor))
    for file in data_dir.rglob("*.json"):
        if file.parent == data_dir:
            continue
        if any(part in RPG_MAKER_SKIP_DIRS for part in file.relative_to(data_dir).parts[:-1]):
            continue
        if file.name in RPG_MAKER_DATABASE_TEXT_FIELDS or file.name in {"System.json", "CommonEvents.json", "Troops.json"} or file.name.startswith("Map"):
            continue
        try:
            data = json.loads(file.read_text(encoding="utf-8-sig"))
        except (json.JSONDecodeError, OSError):
            continue
        entries.extend(_walk_plugin_ui_json(data, file))
    return entries, warnings


def extract_rpg_maker_json_entries(game_dir: Path, plugin_text_extractor=None) -> list[TextEntry]:
    entries, _warnings = extract_rpg_maker_json(game_dir, plugin_text_extractor)
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
            end = path.find("]", i)
            if end == -1:
                token += char
            else:
                idx_str = path[i + 1:end]
                try:
                    parts.append(int(idx_str))
                except ValueError:
                    parts.append(idx_str)
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
    final = parts[-1]
    if isinstance(ref, dict) and final not in ref:
        raise KeyError(final)
    ref[final] = value


def _try_set_json_value(data: Any, path: str, value: str) -> str | None:
    """Set a JSON value, returning a warning string instead of raising for stale paths."""
    try:
        _set_json_value(data, path, value)
    except (IndexError, KeyError, TypeError, ValueError) as exc:
        return f"Invalid translation key {path!r}: {exc}"
    return None


def _source_data_root(source_file: Path) -> Path | None:
    for parent in [source_file.parent, *source_file.parents]:
        if parent.name != "data":
            continue
        if parent.parent.name == "www" or (parent / "System.json").exists() or (parent / "Actors.json").exists():
            return parent
        for child in source_file.relative_to(parent).parts:
            if child.startswith("Map") or child in RPG_MAKER_DATABASE_TEXT_FIELDS or child in {"System.json", "CommonEvents.json", "Troops.json"} or (parent / child).is_dir():
                return parent
    return None


def _apply_output_path(source_file: Path, output_dir: Path) -> Path:
    data_root = _source_data_root(source_file)
    relative = source_file.relative_to(data_root) if data_root is not None else Path(source_file.name)
    if relative.is_absolute() or ".." in relative.parts:
        raise ValueError(f"Unsafe output path for source file: {source_file}")
    target = output_dir / relative
    if not target.resolve().is_relative_to(output_dir.resolve()):
        raise ValueError(f"Output path escapes output folder: {target}")
    return target


def _detect_json_indent(text: str) -> int | None:
    """Detect JSON indentation from the original file text.

    Returns the indent level (2 or 4) if the file is pretty-printed,
    or None if the file is minified (single-line).
    Also handles tab indentation (returns 4 for tabs).
    """
    for line in text.split("\n")[1:4]:
        stripped = line.lstrip()
        if not stripped:
            continue
        if line.startswith("\t"):
            return 4
        spaces = len(line) - len(stripped)
        if spaces > 0:
            return 4 if spaces >= 4 else 2
    return None


def apply_rpg_maker(results: list[TranslationResult], output_dir: Path) -> None:
    grouped: dict[Path, list[TranslationResult]] = {}
    for result in results:
        grouped.setdefault(result.file, []).append(result)
    output_dir.mkdir(parents=True, exist_ok=True)
    targets: dict[Path, Path] = {}
    for file in grouped:
        target = _apply_output_path(file, output_dir)
        resolved = target.resolve()
        if resolved in targets and targets[resolved] != file:
            raise ValueError(f"Multiple source files map to one output path: {targets[resolved]} and {file} -> {target}")
        targets[resolved] = file
    for file, file_results in grouped.items():
        raw_text = file.read_text(encoding="utf-8-sig")
        data = json.loads(raw_text)
        indent = _detect_json_indent(raw_text)
        invalid_rows: list[str] = []
        applied = 0
        for result in file_results:
            if result.sub_keys:
                target_lines = result.target.split("\n")
                for idx, sub_key in enumerate(result.sub_keys):
                    if idx < len(target_lines):
                        line = target_lines[idx]
                    elif target_lines:
                        line = target_lines[-1]
                    else:
                        line = result.source.split("\n")[idx] if idx < len(result.source.split("\n")) else ""
                    warning = _try_set_json_value(data, sub_key, line)
                    if warning is not None:
                        invalid_rows.append(warning)
                    else:
                        applied += 1
            else:
                warning = _try_set_json_value(data, result.key, result.target)
                if warning is not None:
                    invalid_rows.append(warning)
                    continue
                applied += 1
        if invalid_rows:
            for warning in invalid_rows:
                log_event(f"WARN {file.name}: {warning}", level="WARN")
        if applied == 0 and invalid_rows:
            details = "; ".join(invalid_rows[:5])
            if len(invalid_rows) > 5:
                details += f"; ...and {len(invalid_rows) - 5} more"
            raise ValueError(f"No valid translation rows for {file}: {details}")
        target = _apply_output_path(file, output_dir)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(data, ensure_ascii=False, indent=indent), encoding="utf-8")
