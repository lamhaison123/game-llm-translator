from __future__ import annotations

from pathlib import Path

from .models import TextEntry
from .rpg_maker_common import extract_rpg_maker_json


def extract_rpg_maker_mv_detailed(game_dir: Path) -> tuple[list[TextEntry], list[str]]:
    return extract_rpg_maker_json(game_dir)


def extract_rpg_maker_mv(game_dir: Path) -> list[TextEntry]:
    entries, _warnings = extract_rpg_maker_mv_detailed(game_dir)
    return entries
