from __future__ import annotations

from datetime import datetime

from game_llm_translator.path_utils import timestamped_unique_path


def test_timestamped_unique_path_returns_base_when_free(tmp_path):
    when = datetime(2026, 5, 21, 10, 11, 12)

    path = timestamped_unique_path(tmp_path, "data_backup_", when=when)

    assert path == tmp_path / "data_backup_20260521_101112"


def test_timestamped_unique_path_adds_numeric_suffix_on_collision(tmp_path):
    when = datetime(2026, 5, 21, 10, 11, 12)
    (tmp_path / "data_backup_20260521_101112").mkdir()
    (tmp_path / "data_backup_20260521_101112_1").mkdir()

    path = timestamped_unique_path(tmp_path, "data_backup_", when=when)

    assert path == tmp_path / "data_backup_20260521_101112_2"


def test_timestamped_unique_path_preserves_suffix_on_collision(tmp_path):
    when = datetime(2026, 5, 21, 10, 11, 12)
    (tmp_path / "backup_20260521_101112.zip").write_text("exists", encoding="utf-8")

    path = timestamped_unique_path(tmp_path, "backup_", suffix=".zip", when=when)

    assert path == tmp_path / "backup_20260521_101112_1.zip"
