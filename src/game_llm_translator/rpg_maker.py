from __future__ import annotations

from pathlib import Path

from .app_logging import log_event
from .models import TextEntry, TranslationResult
from .rpg_maker_common import (
    _data_dir,
    _parse_path,
    _set_json_value,
    _walk_json,
    apply_rpg_maker as _apply_rpg_maker_json,
    detect_rpg_maker,
    engine_to_gui_game_type,
    gui_game_type_to_engine,
    is_supported_json_engine,
    normalize_gui_game_type,
)
from .rpg_maker_mv import extract_rpg_maker_mv, extract_rpg_maker_mv_detailed
from .rpg_maker_mz import extract_rpg_maker_mz, extract_rpg_maker_mz_detailed


def apply_rpg_maker(results: list[TranslationResult], output_dir: Path) -> None:
    """Apply RPG Maker translations (MV/MZ JSON format)."""
    if not results:
        return
    legacy = [r for r in results if Path(r.file).suffix.lower() == ".rvdata2"]
    if legacy:
        raise ValueError(
            f"Found {len(legacy)} .rvdata2 entries from a previous VX Ace session. "
            "VX Ace support was removed; only MV/MZ .json files can be applied."
        )
    _apply_rpg_maker_json(results, output_dir)


def extract_rpg_maker_detailed(game_dir: Path) -> tuple[list[TextEntry], list[str]]:
    engine = detect_rpg_maker(game_dir)
    if engine in {"xp", "vx", "vx-ace"}:
        raise ValueError(
            f"Detected RPG Maker {engine.upper()}; only MV/MZ are supported. "
            "Use another tool for RGSS1/RGSS2/RGSS3 games or export text manually."
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
