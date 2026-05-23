from __future__ import annotations

from pathlib import Path

from .app_logging import log_event
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
from .rpg_maker_mv import extract_rpg_maker_mv, extract_rpg_maker_mv_detailed
from .rpg_maker_mz import extract_rpg_maker_mz, extract_rpg_maker_mz_detailed


def extract_rpg_maker_detailed(game_dir: Path) -> tuple[list[TextEntry], list[str]]:
    engine = detect_rpg_maker(game_dir)
    if engine in {"xp", "vx", "vx-ace"}:
        raise ValueError(
            f"Detected RPG Maker {engine.upper()}; only MV/MZ JSON (www/data) is supported. "
            "Use another tool for RGSS games or export text manually."
        )
    if engine == "mz":
        return extract_rpg_maker_mz_detailed(game_dir)
    if engine == "mv":
        return extract_rpg_maker_mv_detailed(game_dir)
    if engine == "mv-mz":
        return extract_rpg_maker_mz_detailed(game_dir)
    return [], []


def extract_rpg_maker(game_dir: Path) -> list[TextEntry]:
    entries, warnings = extract_rpg_maker_detailed(game_dir)
    for message in warnings:
        log_event(message, level="WARN")
    return entries
