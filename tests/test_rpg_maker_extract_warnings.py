from __future__ import annotations

import json
from pathlib import Path

from game_llm_translator.rpg_maker_common import extract_rpg_maker_json


def test_extract_reports_invalid_json(tmp_path):
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    (data_dir / "Actors.json").write_text(json.dumps([{"name": "Hero"}]), encoding="utf-8")
    (data_dir / "Broken.json").write_text("{not json", encoding="utf-8")

    entries, warnings = extract_rpg_maker_json(tmp_path)

    assert len(entries) >= 1
    assert any("Broken.json" in w for w in warnings)
