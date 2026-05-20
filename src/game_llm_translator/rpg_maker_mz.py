from __future__ import annotations

from pathlib import Path
from typing import Any

from .models import TextEntry
from .rpg_maker_common import extract_rpg_maker_json_entries

MZ_PLUGIN_TEXT_ARGS = {
    ("TextPicture", "set"): {"text"},
}


def _extract_mz_plugin_text(command: dict[str, Any], file: Path, prefix: str, context_text: str) -> list[TextEntry]:
    if command.get("code") != 357:
        return []
    params = command.get("parameters")
    if not isinstance(params, list) or len(params) < 4 or not isinstance(params[3], dict):
        return []
    plugin = str(params[0])
    command_name = str(params[1])
    fields = MZ_PLUGIN_TEXT_ARGS.get((plugin, command_name), set())
    entries: list[TextEntry] = []
    for field in fields:
        value = params[3].get(field)
        if isinstance(value, str) and value.strip():
            entries.append(TextEntry(file=file, key=f"{prefix}.parameters[3].{field}", source=value, context=f"rpg_maker_mz_plugin_{plugin}_{command_name}_{field}", context_text=context_text))
    return entries


def extract_rpg_maker_mz_detailed(game_dir: Path) -> tuple[list[TextEntry], list[str]]:
    from .rpg_maker_common import extract_rpg_maker_json

    return extract_rpg_maker_json(game_dir, plugin_text_extractor=_extract_mz_plugin_text)


def extract_rpg_maker_mz(game_dir: Path) -> list[TextEntry]:
    entries, _warnings = extract_rpg_maker_mz_detailed(game_dir)
    return entries
