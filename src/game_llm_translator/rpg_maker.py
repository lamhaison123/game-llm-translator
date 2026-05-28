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
from .rpg_maker_vxace import apply_rpg_maker_vxace, extract_rpg_maker_vxace


def apply_rpg_maker(results: list[TranslationResult], output_dir: Path) -> None:
    """Apply RPG Maker translations. Dispatches between JSON (MV/MZ) and
    Marshal (VX Ace) by inspecting result file suffixes.
    """
    if not results:
        return
    vxace = [r for r in results if Path(r.file).suffix.lower() == ".rvdata2"]
    json_like = [r for r in results if Path(r.file).suffix.lower() != ".rvdata2"]
    if json_like:
        _apply_rpg_maker_json(json_like, output_dir)
    if vxace:
        apply_rpg_maker_vxace(vxace, output_dir)


def extract_rpg_maker_detailed(game_dir: Path) -> tuple[list[TextEntry], list[str]]:
    engine = detect_rpg_maker(game_dir)
    if engine == "vx-ace":
        return extract_rpg_maker_vxace(game_dir), []
    if engine in {"xp", "vx"}:
        raise ValueError(
            f"Detected RPG Maker {engine.upper()}; only MV/MZ/VX Ace are supported. "
            "Use another tool for RGSS1/RGSS2 games or export text manually."
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
