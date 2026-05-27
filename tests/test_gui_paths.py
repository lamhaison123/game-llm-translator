from __future__ import annotations

import os

import pytest

from game_llm_translator.gui.paths import normalize_path_text


def test_normalize_empty_string():
    assert normalize_path_text("") == ""


def test_normalize_whitespace_only():
    assert normalize_path_text("   ") == ""


def test_normalize_strips_outer_whitespace():
    result = normalize_path_text("  some/path  ")
    assert not result.startswith(" ")
    assert not result.endswith(" ")


@pytest.mark.skipif(os.name != "nt", reason="Windows-only path separator behavior")
def test_normalize_forward_to_backslash_windows():
    """Qt's QFileDialog returns forward slashes on Windows. Normalize to native."""
    assert normalize_path_text("D:/Games/Foo/Bar") == "D:\\Games\\Foo\\Bar"


@pytest.mark.skipif(os.name != "nt", reason="Windows-only path separator behavior")
def test_normalize_already_native_windows():
    assert normalize_path_text("D:\\Games\\Foo") == "D:\\Games\\Foo"


@pytest.mark.skipif(os.name != "nt", reason="Windows-only path separator behavior")
def test_normalize_mixed_separators_windows():
    assert normalize_path_text("D:/Games\\Foo/Bar") == "D:\\Games\\Foo\\Bar"


@pytest.mark.skipif(os.name == "nt", reason="POSIX-only path separator behavior")
def test_normalize_posix_keeps_forward_slash():
    assert normalize_path_text("/home/user/games") == "/home/user/games"
