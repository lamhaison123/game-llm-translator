from __future__ import annotations

from pathlib import Path

from .models import TextEntry
from .rpg_maker_common import extract_rpg_maker_json


def extract_rpg_maker_mv(game_dir: Path) -> list[TextEntry]:
    return extract_rpg_maker_json(game_dir)
