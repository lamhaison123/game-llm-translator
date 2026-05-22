from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from .models import TextEntry
from .rpg_maker_common import extract_rpg_maker_json_entries

_HEX_COLOR_RE = re.compile(r"^#[0-9a-fA-F]{3,8}$")
_ASSET_EXTENSION_RE = re.compile(r"\.(png|jpg|jpeg|gif|bmp|ogg|mp3|wav|m4a|flac|json|js|csv|txt|xml|html)$", re.IGNORECASE)
_NUMERIC_RE = re.compile(r"^-?\d+(\.\d+)?$")
_CAMEL_OR_SNAKE_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_CAMEL_UPPER_RE = re.compile(r"[a-z][A-Z]|_[A-Za-z]")
_HAS_LETTER_RE = re.compile(r"[A-Za-z\u3040-\u9fff\uac00-\ud7ff\u4e00-\u9fff]")
_HAS_CJK_RE = re.compile(r"[\u3040-\u9fff\uac00-\ud7ff\u4e00-\u9fff]")

_NON_TEXT_FIELD_NAMES = {
    "filename", "file", "picture", "image", "icon", "bitmap",
    "se", "bgm", "bgs", "me", "audio",
    "color", "colour", "tint",
    "x", "y", "z", "width", "height", "scaleX", "scaleY",
    "opacity", "blend", "duration", "speed", "volume", "pitch", "pan",
    "id", "code", "indent", "type", "switch", "variable",
    "actor", "enemy", "troop", "skill", "item", "weapon", "armor",
    "animation", "balloon", "direction", "pattern",
    "condition", "trigger", "through", "priorityType",
    "characterName", "characterIndex", "faceName", "faceIndex",
    # ARPG / combat plugin config fields (not translatable text)
    "hitboxtype", "hitboxtypetype", "subjecthitboxtype", "targethitboxtype",
    "customhitboxtag", "targetcustomhitboxtag",
    "enabled", "enableordisable", "showorhide", "wait",
    "statustype", "hpgauge", "arpgmode",
    "characterspecification", "subjectcharacterspecification",
    "targetcharacterspecification", "eventspecification",
    "isskillspecification", "skillobjectposition", "skillspecification",
    "istargetspecification", "rotationdirection",
    "filterid", "filtertype",
    "graphic", "layer", "layergraphics",
    "eventtags", "value", "arg1", "arg2", "arg3",
    "mode", "blendtype", "visible",
    # ARPG hitbox / geometry fields
    "hitboxlist", "hitbox",
    # Layout / window config fields
    "position", "textsettings", "windowsettings", "backgroundtype",
    "align", "textpadding", "fontsize", "backopacity", "padding",
    # Event reference fields
    "srceventidorname", "target", "leftuporcenter",
    "whenfixed", "whenbyvariables",
    # NRP_BattleEventEXMZ / battle plugin fields (JS code / identifiers)
    "subject", "targetside", "targetcondition", "targetfiltertype",
    "subjectcondition", "conditiontype", "conditionswitch",
    "switchno", "switchno2", "variableid", "variablevalue",
    "targettype", "subjecttype", "forcetype",
    # Shop / inventory plugin fields (identifiers, not translatable)
    "shopname", "shopid", "stockid", "itemid", "itemid2",
    # UI / visual plugin fields (asset references and identifiers)
    "windowbgimage", "buttonbgimage", "buttonfocusimage",
    "templateid", "positiontype", "darkness",
    # Input / form plugin fields
    "placeholder", "inputtype", "defaultvalue",
    # Common non-text identifiers
    "tag", "category", "key", "nameid",
    # Animation / effect fields
    "animationid", "animationtype", "effecttype",
    # Variable / switch references
    "switchid", "variable", "variablenumber",
    "canceltype", "iniposition", "maxcols", "windowwidth",
    "itemheightadjust", "buttonimageadjustx", "buttonimageadjusty",
    "iconindex",
}

_ENUM_VALUE_BLACKLIST = {
    "true", "false",
    "hp", "mp", "tp",
    "attack", "damage", "custom", "normal",
    "left", "right", "up", "down", "center",
    "none", "auto", "on", "off",
    "all", "self", "target",
    "subject", "opponents", "opponentsunit", "actors", "actorsunit",
    "friend", "friendunit",
}

_JS_CODE_RE = re.compile(
    r"(?:\$game[A-Z]|a\.|b\.|this\.|Math\.|JSON\.|console\.|function\s*\(|=>|"
    r"\.isState|\.isActor|\.isEnemy|\.index\(\)|\.hp\s|\.mp\s|\.tp\s|"
    r"\.value\(\)|\.gainHp|\.gainMp|\.addItem|\.removeItem|"
    r"DataManager|SceneManager|BattleManager|Game_|AudioManager|"
    r"isNaN\(|typeof\s|instanceof\s|\.length\s*[<>=]|===|!==)"
)

_COMMENT_SUFFIX_RE = re.compile(r"\s*#[^\n]+$")

_ASSET_SLASH_RE = re.compile(r"^[\w\-./\\]+\.(png|jpg|jpeg|gif|bmp|ogg|mp3|wav|m4a|flac|json|js|csv|txt|xml|html)$", re.IGNORECASE)

_IDENTIFIER_PATTERN_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*(/[A-Za-z_][A-Za-z0-9_]*)*$")

_RPG_MAKER_INTERNAL_VALUE_RE = re.compile(
    r"^(?:subject|opponents|self|target|friend|actors|none|auto|"
    r"normal|custom|left|right|up|down|center|all)(?:\s*#.*)?$",
    re.IGNORECASE,
)


def _looks_like_translatable_text(value: str) -> bool:
    """Return True if the string value is likely human-readable text worth translating."""
    if not value or not value.strip():
        return False
    v = value.strip()
    if len(v) < 2:
        return False
    if _HEX_COLOR_RE.match(v):
        return False
    if _NUMERIC_RE.match(v):
        return False
    if _ASSET_EXTENSION_RE.search(v):
        return False
    if _ASSET_SLASH_RE.match(v):
        return False
    if not _HAS_LETTER_RE.search(v):
        return False
    stripped_to_comment = _COMMENT_SUFFIX_RE.sub("", v).strip()
    if _RPG_MAKER_INTERNAL_VALUE_RE.match(stripped_to_comment):
        return False
    if v.lower() in _ENUM_VALUE_BLACKLIST:
        return False
    if _JS_CODE_RE.search(stripped_to_comment):
        return False
    if (v.startswith("[") and v.endswith("]")) or (v.startswith("{") and v.endswith("}")):
        return False
    if v.startswith('"') and v.endswith('"') and len(v) <= 40:
        return False
    if _CAMEL_OR_SNAKE_RE.match(v) and len(v) <= 32 and (_CAMEL_UPPER_RE.search(v) or "_" in v):
        return False
    if _IDENTIFIER_PATTERN_RE.match(v) and len(v) <= 40:
        if not _HAS_CJK_RE.search(v):
            has_upper = any(c.isupper() for c in v)
            has_lower = any(c.islower() for c in v)
            has_underscore = "_" in v
            has_slash = "/" in v
            if has_slash or has_underscore or (not has_upper and has_lower):
                return False
    return True


_PLUGIN_TEXT_FIELD_OVERRIDES: dict[str, dict[str, list[str]]] = {
    "NRP_BattleEventEXMZ": {
        "forceSubject": ["message"],
        "setConditionSwitch": [],
        "forceTargetFilter": [],
        "forceAction": [],
        "clearForce": [],
        "setBattleEventEX": [],
    },
    "TorigoyaMZ_NotifyMessage": {
        "notify": ["message"],
    },
    "LL_GalgeChoiceWindow": {
        "showChoice": ["choices"],
    },
    "MNKR_SupponShopStockMZ": {
        "addItem": [],
        "openShop": [],
        "closeShop": [],
        "deleteShop": [],
        "makeShop": [],
    },
    "DirectivityShake": [],
    "FilterControllerMZ": [],
    "AnimationByPoint": [],
    "MPP_MapLight": {
        "setDarkness": [],
    },
    "NovelGameUI": [],
    "ITB_DisableKeyInputs": [],
    "111_InputForm": {
        "show": [],
    },
    "PictureAnimation": [],
    "PANDA_ExNumberInput": [],
    "DTextPicture": {
        "dText": ["text"],
    },
}


def _extract_mz_plugin_text(command: dict[str, Any], file: Path, prefix: str, context_text: str) -> list[TextEntry]:
    if command.get("code") != 357:
        return []
    params = command.get("parameters")
    if not isinstance(params, list) or len(params) < 4 or not isinstance(params[3], dict):
        return []
    plugin = str(params[0])
    command_name = str(params[1])
    args: dict[str, Any] = params[3]
    plugin_overrides = _PLUGIN_TEXT_FIELD_OVERRIDES.get(plugin)
    if plugin_overrides is not None:
        if isinstance(plugin_overrides, list):
            return []
        cmd_fields = plugin_overrides.get(command_name)
        if cmd_fields is not None and not cmd_fields:
            return []
    entries: list[TextEntry] = []
    for field, value in args.items():
        if field.lower() in _NON_TEXT_FIELD_NAMES:
            continue
        if plugin_overrides is not None and isinstance(plugin_overrides, dict):
            cmd_fields = plugin_overrides.get(command_name)
            if cmd_fields is not None and field not in cmd_fields:
                continue
        if not isinstance(value, str):
            continue
        if not _looks_like_translatable_text(value):
            continue
        entries.append(TextEntry(
            file=file,
            key=f"{prefix}.parameters[3].{field}",
            source=value,
            context=f"rpg_maker_mz_plugin_{plugin}_{command_name}_{field}",
            context_text=context_text,
        ))
    return entries


def extract_rpg_maker_mz_detailed(game_dir: Path) -> tuple[list[TextEntry], list[str]]:
    from .rpg_maker_common import extract_rpg_maker_json

    return extract_rpg_maker_json(game_dir, plugin_text_extractor=_extract_mz_plugin_text)


def extract_rpg_maker_mz(game_dir: Path) -> list[TextEntry]:
    entries, _warnings = extract_rpg_maker_mz_detailed(game_dir)
    return entries
