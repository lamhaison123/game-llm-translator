"""RPG Maker VX Ace (.rvdata2 Ruby Marshal) extract + apply.

VX Ace data files are Ruby Marshal 4.8 dumps of RPG::* objects. We use a vendored
codec (rm_marshal_core.MC/ME) that round-trips byte-equal — critical because the
game engine reads these files binary-byte-by-byte.

Pipeline:
  extract_rpg_maker_vxace(game_dir)   -> list[TextEntry]    (Japanese strings + key paths)
  apply_rpg_maker_vxace(results, out) -> writes translated .rvdata2 files

Key path format:
  $.attr           - object instance variable (the '@' prefix is stripped for readability)
  $[N]             - array index OR integer-keyed hash entry
  $.foo.bar[0]     - chained
Example: $.events[1].pages[0].list[12].parameters[0]
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

from .app_logging import log_event
from .models import TextEntry, TranslationResult
from .vendor.rm_marshal_core import MC, ME


@dataclass(slots=True)
class VxAceApplySummary:
    applied: int = 0
    skipped: int = 0
    files_written: int = 0


# ---- file inventory ----

_DATA_FILES_DATABASE: dict[str, dict[str, str]] = {
    "actors.rvdata2":    {"name": "name", "nickname": "nickname", "profile": "profile", "description": "description", "note": "note"},
    "classes.rvdata2":   {"name": "name", "note": "note"},
    "skills.rvdata2":    {"name": "name", "description": "description", "message1": "message", "message2": "message", "note": "note"},
    "items.rvdata2":     {"name": "name", "description": "description", "note": "note"},
    "weapons.rvdata2":   {"name": "name", "description": "description", "note": "note"},
    "armors.rvdata2":    {"name": "name", "description": "description", "note": "note"},
    "enemies.rvdata2":   {"name": "name", "note": "note"},
    "states.rvdata2":    {"name": "name", "message1": "message", "message2": "message", "message3": "message", "message4": "message", "note": "note"},
    "animations.rvdata2": {"name": "name"},
    "tilesets.rvdata2":  {"name": "name", "note": "note"},
}

# VX Ace event commands worth translating. Differs from MV/MZ:
#   - 101: header only (face/pos), NO text param
#   - 102: parameters[0] is array of choice strings
#   - 402: parameters[1] is choice label
#   - 105: scroll-text header; 405: scroll continuation
#   - 108/408: comment
#   - 320: change actor name (parameters[1])
#   - 324: change actor nickname (parameters[1])
#   - 325: change actor profile (parameters[1])
#   - 355/655: Ruby script (extracted when payload contains CJK; LLM rewrite is risky,
#             so callers should review these entries before applying)
_EVENT_CONTINUATION_PARENT_CODES = {101, 105}  # 401 belongs to 101, 405 to 105

# Strings shorter than this with no JP/CJK characters are skipped (likely identifier).
_HAS_TRANSLATABLE_RE = re.compile(r"[぀-ヿ一-鿿가-퟿ｦ-ﾝ]")

_VXACE_NOTE_CONTEXT = "rpg_maker_vxace_note"


# ---- ME helpers ----

def _is_string(me: ME | None) -> bool:
    return me is not None and me.at().token == b'"'


def _is_object(me: ME | None) -> bool:
    return me is not None and me.at().token == b"o"


def _is_array(me: ME | None) -> bool:
    return me is not None and me.at().token == b"["


def _is_hash(me: ME | None) -> bool:
    return me is not None and me.at().token == b"{"


def _decode_string(me: ME) -> str | None:
    """Return UTF-8 string from a Marshal string ME, or None if it can't decode."""
    me = me.at()
    if me.token != b'"':
        return None
    try:
        return me.data.decode("utf-8")
    except UnicodeDecodeError:
        return None


def _object_class_name(me: ME) -> str:
    """RPG::Foo style class name from an 'o' object's symbol part."""
    me = me.at()
    if me.token != b"o":
        return ""
    sym = me.data[0].at()
    return sym.data.decode("utf-8", errors="replace")


def _object_get(me: ME, ivar_name: str) -> ME | None:
    """Look up `@ivar` on an object ME. Returns child ME or None.

    `ivar_name` is the bare attribute name without `@`.
    """
    me = me.at()
    if me.token != b"o":
        return None
    table = me.data[1].data
    key = f"@{ivar_name}".encode("utf-8")
    pair = table.get(key)
    if pair is None:
        return None
    return pair[1]


def _object_ivars(me: ME) -> Iterable[tuple[str, ME]]:
    """Yield (ivar_name_without_@, value_me) for each instance variable."""
    me = me.at()
    if me.token != b"o":
        return
    for raw_key, (_key_me, value_me) in me.data[1].data.items():
        if isinstance(raw_key, bytes) and raw_key.startswith(b"@"):
            yield raw_key[1:].decode("utf-8", errors="replace"), value_me


def _array_items(me: ME) -> list[ME]:
    me = me.at()
    return me.data if me.token == b"[" else []


def _hash_items(me: ME) -> list[tuple, ME]:
    """Return list of (raw_key, value_me) for a hashtable ME."""
    me = me.at()
    if me.token != b"{":
        return []
    return [(raw_key, value_me) for raw_key, (_k, value_me) in me.data.items()]


def _is_translatable(value: str) -> bool:
    if not value or not value.strip():
        return False
    return bool(_HAS_TRANSLATABLE_RE.search(value))


def _note_extract_mode(value: str) -> str:
    text = value.strip()
    if not text or not _HAS_TRANSLATABLE_RE.search(text):
        return "skip"
    tag_bodies = re.findall(r"<([^<>]*)>", text)
    without_tags = re.sub(r"<[^<>]*>", "", text).strip()
    if tag_bodies:
        for body in tag_bodies:
            body = body.strip()
            if _HAS_TRANSLATABLE_RE.search(body) and (re.search(r"[:=,]", body) or re.search(r"\d", body)):
                return "skip"
        if not without_tags:
            return "whole"
    return "whole"


def _note_is_translatable(value: str) -> bool:
    return _note_extract_mode(value) == "whole"


def _ruby_string_literals(line: str) -> list[tuple[int, str]]:
    out: list[tuple[int, str]] = []
    i = 0
    literal_index = 0
    while i < len(line):
        quote = line[i]
        if quote not in {'"', "'"}:
            i += 1
            continue
        i += 1
        chars: list[str] = []
        while i < len(line):
            ch = line[i]
            if ch == "\\" and i + 1 < len(line):
                chars.append(line[i + 1])
                i += 2
                continue
            if ch == quote:
                break
            chars.append(ch)
            i += 1
        text = "".join(chars)
        if _is_translatable(text):
            out.append((literal_index, text))
        literal_index += 1
        i += 1
    return out


def _replace_ruby_string_literal(line: str, target_index: int, replacement: str) -> str:
    result: list[str] = []
    i = 0
    literal_index = 0
    while i < len(line):
        quote = line[i]
        if quote not in {'"', "'"}:
            result.append(line[i])
            i += 1
            continue
        result.append(quote)
        i += 1
        original: list[str] = []
        while i < len(line):
            ch = line[i]
            if ch == "\\" and i + 1 < len(line):
                original.append(line[i])
                original.append(line[i + 1])
                i += 2
                continue
            if ch == quote:
                break
            original.append(ch)
            i += 1
        if literal_index == target_index:
            escaped = replacement.replace("\\", "\\\\").replace(quote, "\\" + quote)
            result.append(escaped)
        else:
            result.extend(original)
        if i < len(line) and line[i] == quote:
            result.append(quote)
            i += 1
        literal_index += 1
    return "".join(result)


# ---- key path ----

_PATH_TOKEN_RE = re.compile(r"\.([A-Za-z_][A-Za-z0-9_]*)|\[(-?\d+)\]")


def _parse_path(path: str) -> list[tuple[str, str | int]]:
    """Tokenize a key path. Returns list of ('attr', name) or ('idx', int) steps.

    Path grammar: starts with `$`, then sequence of `.name` (attribute) or
    `[N]` (numeric index or hash int-key).
    """
    if not path.startswith("$"):
        raise ValueError(f"Path must start with '$': {path}")
    steps: list[tuple[str, str | int]] = []
    rest = path[1:]
    pos = 0
    while pos < len(rest):
        m = _PATH_TOKEN_RE.match(rest, pos)
        if not m:
            raise ValueError(f"Cannot parse path at offset {pos}: {path}")
        if m.group(1) is not None:
            steps.append(("attr", m.group(1)))
        else:
            steps.append(("idx", int(m.group(2))))
        pos = m.end()
    return steps


def _resolve_path(root: ME, steps: list[tuple[str, str | int]]) -> ME | None:
    """Walk the key-path steps from root, returning the target ME (or None)."""
    node: ME | None = root
    for kind, value in steps:
        if node is None:
            return None
        node = node.at()
        if kind == "attr":
            node = _object_get(node, str(value))
        else:
            target = node.at()
            if target.token == b"[":
                idx = int(value)
                if 0 <= idx < len(target.data):
                    node = target.data[idx]
                else:
                    return None
            elif target.token == b"{":
                pair = target.data.get(int(value))
                if pair is None:
                    return None
                node = pair[1]
            else:
                return None
    return node


# ---- event command walker ----

def _walk_event_list(list_me: ME, file: Path, prefix: str, context_prefix: str, entries: list[TextEntry], context_text: str = "") -> None:
    """Walk an `@list` array of RPG::EventCommand. Extract dialogue/choices/etc."""
    commands = _array_items(list_me)
    # Pre-compute the merged dialogue context_text for this list (best-effort speaker hints).
    if not context_text:
        context_text = _build_event_context_text(commands)
    for i, cmd_me in enumerate(commands):
        cmd_me = cmd_me.at()
        if cmd_me.token != b"o":
            continue
        code_me = _object_get(cmd_me, "code")
        params_me = _object_get(cmd_me, "parameters")
        if code_me is None or params_me is None:
            continue
        code = code_me.at().data
        if not isinstance(code, int):
            continue
        params = _array_items(params_me)
        cmd_prefix = f"{prefix}[{i}].parameters"
        if code == 401 and params and _is_string(params[0]):
            s = _decode_string(params[0])
            if s and _is_translatable(s):
                entries.append(TextEntry(
                    file=file, key=f"{cmd_prefix}[0]", source=s,
                    context=f"{context_prefix}_dialogue", context_text=context_text,
                ))
        elif code == 405 and params and _is_string(params[0]):
            s = _decode_string(params[0])
            if s and _is_translatable(s):
                entries.append(TextEntry(
                    file=file, key=f"{cmd_prefix}[0]", source=s,
                    context=f"{context_prefix}_scroll_text", context_text=context_text,
                ))
        elif code == 102 and params and _is_array(params[0]):
            for j, choice_me in enumerate(_array_items(params[0])):
                if _is_string(choice_me):
                    s = _decode_string(choice_me)
                    if s and _is_translatable(s):
                        entries.append(TextEntry(
                            file=file, key=f"{cmd_prefix}[0][{j}]", source=s,
                            context=f"{context_prefix}_choice", context_text=context_text,
                        ))
        elif code == 402 and len(params) >= 2 and _is_string(params[1]):
            # Choice label (mirror of 102 entry) — keep them in sync; extract so user can re-translate it.
            s = _decode_string(params[1])
            if s and _is_translatable(s):
                entries.append(TextEntry(
                    file=file, key=f"{cmd_prefix}[1]", source=s,
                    context=f"{context_prefix}_choice_label", context_text=context_text,
                ))
        elif code in (108, 408) and params and _is_string(params[0]):
            s = _decode_string(params[0])
            if s and _is_translatable(s):
                entries.append(TextEntry(
                    file=file, key=f"{cmd_prefix}[0]", source=s,
                    context=f"{context_prefix}_comment", context_text=context_text,
                ))
        elif code in (320, 324, 325) and len(params) >= 2 and _is_string(params[1]):
            s = _decode_string(params[1])
            if s and _is_translatable(s):
                ctx_suffix = {320: "actor_name", 324: "actor_nickname", 325: "actor_profile"}[code]
                entries.append(TextEntry(
                    file=file, key=f"{cmd_prefix}[1]", source=s,
                    context=f"{context_prefix}_{ctx_suffix}", context_text=context_text,
                ))
        elif code in (355, 655) and params and _is_string(params[0]):
            s = _decode_string(params[0])
            if not s:
                continue
            for literal_idx, literal_text in _ruby_string_literals(s):
                entries.append(TextEntry(
                    file=file, key=f"{cmd_prefix}[0].ruby_string[{literal_idx}]", source=literal_text,
                    context=f"{context_prefix}_script_string", context_text=s,
                ))


def _build_event_context_text(commands: list[ME]) -> str:
    """Concatenate dialogue lines (401) within an event list for translator context."""
    parts: list[str] = []
    for cmd_me in commands:
        cmd_me = cmd_me.at()
        if cmd_me.token != b"o":
            continue
        code_me = _object_get(cmd_me, "code")
        params_me = _object_get(cmd_me, "parameters")
        if code_me is None or params_me is None:
            continue
        code = code_me.at().data
        params = _array_items(params_me)
        if code == 401 and params and _is_string(params[0]):
            s = _decode_string(params[0])
            if s and s not in parts:
                parts.append(s)
        elif code == 102 and params and _is_array(params[0]):
            for c in _array_items(params[0]):
                if _is_string(c):
                    s = _decode_string(c)
                    if s and s not in parts:
                        parts.append(s)
        if len(parts) >= 16:
            break
    return "\n".join(parts)


# ---- per-file walkers ----

def _walk_map(root: ME, file: Path, entries: list[TextEntry]) -> None:
    if not _is_object(root):
        return
    display_name = _object_get(root, "display_name")
    if _is_string(display_name):
        s = _decode_string(display_name)
        if s and _is_translatable(s):
            entries.append(TextEntry(file=file, key="$.display_name", source=s, context="rpg_maker_map_display_name"))
    note = _object_get(root, "note")
    if _is_string(note):
        s = _decode_string(note)
        if s and _note_is_translatable(s):
            entries.append(TextEntry(file=file, key="$.note", source=s, context=_VXACE_NOTE_CONTEXT))
    events_me = _object_get(root, "events")
    if not _is_hash(events_me):
        return
    for event_id, event_me in _hash_items(events_me):
        if not isinstance(event_id, int) or not _is_object(event_me):
            continue
        event_prefix = f"$.events[{event_id}]"
        event_name = _object_get(event_me, "name")
        if _is_string(event_name):
            s = _decode_string(event_name)
            if s and _is_translatable(s):
                entries.append(TextEntry(file=file, key=f"{event_prefix}.name", source=s, context="rpg_maker_map_event_name"))
        pages_me = _object_get(event_me, "pages")
        for page_idx, page_me in enumerate(_array_items(pages_me) if pages_me else []):
            list_me = _object_get(page_me, "list")
            if _is_array(list_me):
                _walk_event_list(
                    list_me, file,
                    f"{event_prefix}.pages[{page_idx}].list",
                    "rpg_maker_map",
                    entries,
                )


def _walk_mapinfos(root: ME, file: Path, entries: list[TextEntry]) -> None:
    if not _is_hash(root):
        return
    for key, info_me in _hash_items(root):
        if not isinstance(key, int) or not _is_object(info_me):
            continue
        name_me = _object_get(info_me, "name")
        if _is_string(name_me):
            s = _decode_string(name_me)
            if s and _is_translatable(s):
                entries.append(TextEntry(file=file, key=f"$[{key}].name", source=s, context="rpg_maker_map_info_name"))


def _walk_common_events(root: ME, file: Path, entries: list[TextEntry]) -> None:
    for idx, ce_me in enumerate(_array_items(root)):
        if not _is_object(ce_me):
            continue
        name_me = _object_get(ce_me, "name")
        if _is_string(name_me):
            s = _decode_string(name_me)
            if s and _is_translatable(s):
                entries.append(TextEntry(file=file, key=f"$[{idx}].name", source=s, context="rpg_maker_common_event_name"))
        list_me = _object_get(ce_me, "list")
        if _is_array(list_me):
            _walk_event_list(list_me, file, f"$[{idx}].list", "rpg_maker_common_event", entries)


def _walk_troops(root: ME, file: Path, entries: list[TextEntry]) -> None:
    for idx, troop_me in enumerate(_array_items(root)):
        if not _is_object(troop_me):
            continue
        name_me = _object_get(troop_me, "name")
        if _is_string(name_me):
            s = _decode_string(name_me)
            if s and _is_translatable(s):
                entries.append(TextEntry(file=file, key=f"$[{idx}].name", source=s, context="rpg_maker_troop_name"))
        pages_me = _object_get(troop_me, "pages")
        for page_idx, page_me in enumerate(_array_items(pages_me) if pages_me else []):
            list_me = _object_get(page_me, "list")
            if _is_array(list_me):
                _walk_event_list(
                    list_me, file,
                    f"$[{idx}].pages[{page_idx}].list",
                    "rpg_maker_troop",
                    entries,
                )


_SYSTEM_STRING_FIELDS = ("game_title", "currency_unit")
_SYSTEM_ARRAY_FIELDS = (
    ("elements", "rpg_maker_system_element"),
    ("skill_types", "rpg_maker_system_skill_type"),
    ("weapon_types", "rpg_maker_system_weapon_type"),
    ("armor_types", "rpg_maker_system_armor_type"),
    ("switches", "rpg_maker_system_switch"),
    ("variables", "rpg_maker_system_variable"),
)
_TERMS_FIELDS = (
    ("basic", "rpg_maker_terms_basic"),
    ("params", "rpg_maker_terms_params"),
    ("etypes", "rpg_maker_terms_etypes"),
    ("commands", "rpg_maker_terms_commands"),
    ("messages", "rpg_maker_terms_messages"),
)


def _walk_system(root: ME, file: Path, entries: list[TextEntry]) -> None:
    if not _is_object(root):
        return
    for ivar in _SYSTEM_STRING_FIELDS:
        child = _object_get(root, ivar)
        if _is_string(child):
            s = _decode_string(child)
            if s and _is_translatable(s):
                entries.append(TextEntry(file=file, key=f"$.{ivar}", source=s, context=f"rpg_maker_system_{ivar}"))
    for ivar, context in _SYSTEM_ARRAY_FIELDS:
        arr = _object_get(root, ivar)
        if _is_array(arr):
            for i, item_me in enumerate(_array_items(arr)):
                if _is_string(item_me):
                    s = _decode_string(item_me)
                    if s and _is_translatable(s):
                        entries.append(TextEntry(file=file, key=f"$.{ivar}[{i}]", source=s, context=context))
    terms_me = _object_get(root, "terms")
    if _is_object(terms_me):
        for ivar, context in _TERMS_FIELDS:
            arr = _object_get(terms_me, ivar)
            if _is_array(arr):
                for i, item_me in enumerate(_array_items(arr)):
                    if _is_string(item_me):
                        s = _decode_string(item_me)
                        if s and _is_translatable(s):
                            entries.append(TextEntry(file=file, key=f"$.terms.{ivar}[{i}]", source=s, context=context))


def _walk_database(root: ME, file: Path, fields: dict[str, str], entries: list[TextEntry]) -> None:
    """Database walker for Actors/Items/Skills/etc. — arrays of RPG::* objects."""
    context_prefix = f"rpg_maker_{file.stem.lower()}"
    for idx, item_me in enumerate(_array_items(root)):
        if not _is_object(item_me):
            continue
        for ivar, kind in fields.items():
            child = _object_get(item_me, ivar)
            if not _is_string(child):
                continue
            s = _decode_string(child)
            if not s:
                continue
            if ivar == "note":
                if not _note_is_translatable(s):
                    continue
                entries.append(TextEntry(file=file, key=f"$[{idx}].{ivar}", source=s, context=_VXACE_NOTE_CONTEXT))
            else:
                if not _is_translatable(s):
                    continue
                entries.append(TextEntry(file=file, key=f"$[{idx}].{ivar}", source=s, context=f"{context_prefix}_{kind}"))


# ---- public extract ----

def _data_dir(game_dir: Path) -> Path:
    return game_dir / "Data"


def extract_rpg_maker_vxace(game_dir: Path) -> list[TextEntry]:
    """Extract translatable text from an RPG Maker VX Ace game."""
    data_dir = _data_dir(game_dir)
    if not data_dir.is_dir():
        log_event(f"VX Ace Data folder not found: {data_dir}", level="WARN")
        return []
    entries: list[TextEntry] = []
    for path in sorted(data_dir.iterdir()):
        if not path.is_file() or path.suffix.lower() != ".rvdata2":
            continue
        name_lower = path.name.lower()
        try:
            mc = MC.load(path.read_bytes())
        except Exception as exc:
            log_event(f"Failed to load {path.name}: {exc}", level="WARN")
            continue
        root = mc.root
        if root is None:
            continue
        if name_lower == "system.rvdata2":
            _walk_system(root, path, entries)
        elif name_lower == "mapinfos.rvdata2":
            _walk_mapinfos(root, path, entries)
        elif name_lower == "commonevents.rvdata2":
            _walk_common_events(root, path, entries)
        elif name_lower == "troops.rvdata2":
            _walk_troops(root, path, entries)
        elif name_lower == "scripts.rvdata2":
            # Embedded Ruby source; deferred to a future pass.
            continue
        elif name_lower in _DATA_FILES_DATABASE:
            _walk_database(root, path, _DATA_FILES_DATABASE[name_lower], entries)
        elif re.fullmatch(r"map\d{3,4}\.rvdata2", name_lower):
            _walk_map(root, path, entries)
        # else: unknown file, skip
    return entries


# ---- apply ----

def _set_string_me(node: ME, new_value: str) -> bool:
    """Replace a Marshal string ME's payload with new UTF-8 bytes.

    Preserves any instance variables (e.g. encoding flag) on the string.
    Returns True on success.
    """
    target = node.at()
    if target.token != b'"':
        return False
    target.data = new_value.encode("utf-8")
    return True


def apply_rpg_maker_vxace(results: list[TranslationResult], output_dir: Path) -> VxAceApplySummary:
    """Apply translated entries by rewriting .rvdata2 files into `output_dir/`.

    Files are written flat (e.g. `output_dir/Map001.rvdata2`) to match the
    convention used by `apply_rpg_maker` for MV/MZ.
    """
    summary = VxAceApplySummary()
    grouped: dict[Path, list[TranslationResult]] = {}
    for r in results:
        if r.target is None or r.target == r.source:
            continue
        grouped.setdefault(r.file, []).append(r)
    if not grouped:
        return summary

    output_dir.mkdir(parents=True, exist_ok=True)

    for src_path, items in grouped.items():
        try:
            mc = MC.load(src_path.read_bytes())
        except Exception as exc:
            log_event(f"apply: failed to load {src_path.name}: {exc}", level="WARN")
            summary.skipped += len(items)
            continue
        root = mc.root
        if root is None:
            summary.skipped += len(items)
            continue
        applied = 0
        skipped = 0
        for r in items:
            ruby_match = re.fullmatch(r"(.+)\.ruby_string\[(\d+)\]", r.key)
            if ruby_match:
                try:
                    steps = _parse_path(ruby_match.group(1))
                except ValueError as exc:
                    log_event(f"apply: bad path {r.key} in {src_path.name}: {exc}", level="WARN")
                    skipped += 1
                    continue
                node = _resolve_path(root, steps)
                if node is None or not _is_string(node):
                    skipped += 1
                    continue
                current = _decode_string(node)
                if current is None:
                    skipped += 1
                    continue
                updated = _replace_ruby_string_literal(current, int(ruby_match.group(2)), r.target)
                if _set_string_me(node, updated):
                    applied += 1
                else:
                    skipped += 1
                continue
            try:
                steps = _parse_path(r.key)
            except ValueError as exc:
                log_event(f"apply: bad path {r.key} in {src_path.name}: {exc}", level="WARN")
                skipped += 1
                continue
            node = _resolve_path(root, steps)
            if node is None or not _is_string(node):
                skipped += 1
                continue
            if _set_string_me(node, r.target):
                applied += 1
            else:
                skipped += 1
        out_path = output_dir / src_path.name
        try:
            out_path.write_bytes(mc.dump())
        except Exception as exc:
            log_event(f"apply: failed to write {out_path}: {exc}", level="ERROR")
            summary.skipped += skipped + applied
            continue
        summary.files_written += 1
        summary.applied += applied
        summary.skipped += skipped
        if skipped:
            log_event(f"apply: {src_path.name}: applied={applied} skipped={skipped}", level="INFO")
    return summary
