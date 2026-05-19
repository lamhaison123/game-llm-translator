from __future__ import annotations

from pathlib import Path

from .models import TextEntry
from .rpg_maker_common import (
    _data_dir,
    _parse_path,
    _set_json_value,
    _walk_json,
    apply_rpg_maker,
    detect_rpg_maker,
    engine_to_gui_game_type,
    gui_game_type_to_engine,
    is_supported_json_engine,
    normalize_gui_game_type,
)
from .rpg_maker_mv import extract_rpg_maker_mv
from .rpg_maker_mz import extract_rpg_maker_mz


def extract_rpg_maker(game_dir: Path) -> list[TextEntry]:
    engine = detect_rpg_maker(game_dir)
    if engine == "mz":
        return extract_rpg_maker_mz(game_dir)
    if engine in {"mv", "mv-mz"}:
        return extract_rpg_maker_mv(game_dir)
    return []
