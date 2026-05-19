from __future__ import annotations

from pathlib import Path

import pytest


def test_log_event_creates_file(tmp_path, monkeypatch):
    from game_llm_translator import app_config
    monkeypatch.setattr(app_config, "app_data_dir", lambda: tmp_path)

    from game_llm_translator import app_logging
    monkeypatch.setattr(app_logging, "logs_dir", lambda: tmp_path / "logs")

    app_logging.log_event("Test message")

    log_files = list((tmp_path / "logs").glob("*.log"))
    assert len(log_files) == 1
    content = log_files[0].read_text(encoding="utf-8")
    assert "[INFO] Test message" in content


def test_log_event_sanitizes_newlines(tmp_path, monkeypatch):
    from game_llm_translator import app_logging
    monkeypatch.setattr(app_logging, "logs_dir", lambda: tmp_path / "logs")

    app_logging.log_event("Line1\nLine2\r\nLine3")

    log_files = list((tmp_path / "logs").glob("*.log"))
    content = log_files[0].read_text(encoding="utf-8")
    assert "\n" not in content.split("[INFO]")[1].split("\n")[0]
    assert "Line1 Line2  Line3" in content


def test_log_event_custom_level(tmp_path, monkeypatch):
    from game_llm_translator import app_logging
    monkeypatch.setattr(app_logging, "logs_dir", lambda: tmp_path / "logs")

    app_logging.log_event("Warning message", level="WARNING")

    content = (list((tmp_path / "logs").glob("*.log"))[0]).read_text(encoding="utf-8")
    assert "[WARNING] Warning message" in content


def test_log_event_does_not_raise_on_error(tmp_path, monkeypatch):
    """log_event must never crash the caller even if writing fails."""
    from game_llm_translator import app_logging

    def bad_logs_dir():
        # Return a path under a non-existent read-only location
        return Path("/this/should/not/exist/logs")

    monkeypatch.setattr(app_logging, "logs_dir", bad_logs_dir)
    # Should not raise
    app_logging.log_event("Should be silent")


def test_log_event_appends(tmp_path, monkeypatch):
    from game_llm_translator import app_logging
    monkeypatch.setattr(app_logging, "logs_dir", lambda: tmp_path / "logs")

    app_logging.log_event("First")
    app_logging.log_event("Second")

    content = (list((tmp_path / "logs").glob("*.log"))[0]).read_text(encoding="utf-8")
    assert "First" in content
    assert "Second" in content
