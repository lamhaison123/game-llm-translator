from __future__ import annotations

from pathlib import Path

import pytest

from game_llm_translator.models import TranslationResult
from game_llm_translator.xunity import (
    _is_translatable_line,
    _parse_line,
    apply_xunity,
    detect_xunity,
    extract_xunity,
)


# ---------------------------------------------------------------------------
# detect_xunity
# ---------------------------------------------------------------------------


def test_detect_xunity_finds_root_translation_dir(tmp_path):
    (tmp_path / "Translation").mkdir()
    assert detect_xunity(tmp_path) == tmp_path / "Translation"


def test_detect_xunity_finds_bepinex_translation_dir(tmp_path):
    (tmp_path / "BepInEx" / "Translation").mkdir(parents=True)
    assert detect_xunity(tmp_path) == tmp_path / "BepInEx" / "Translation"


def test_detect_xunity_returns_none_when_missing(tmp_path):
    assert detect_xunity(tmp_path) is None


# ---------------------------------------------------------------------------
# _is_translatable_line / _parse_line
# ---------------------------------------------------------------------------


def test_is_translatable_line_basic():
    assert _is_translatable_line("こんにちは=Hello")
    assert _is_translatable_line("シンプルリング=Simple Ring")


def test_is_translatable_line_skips_directives_and_regex():
    assert not _is_translatable_line("#set level 1,2,3")
    assert not _is_translatable_line('r:"^\\[ITEM\\]"=ITEM')
    assert not _is_translatable_line('sr:"^foo$"=bar')
    assert not _is_translatable_line("")
    assert not _is_translatable_line("   ")
    assert not _is_translatable_line("no equals here")


def test_parse_line_returns_pair():
    assert _parse_line("こんにちは=Hello") == ("こんにちは", "Hello")
    assert _parse_line("foo=") == ("foo", "")


def test_parse_line_skips_directives():
    assert _parse_line("#enable fallback") is None


def test_parse_line_handles_multiple_equals():
    """Only first = is the separator; subsequent ones go into translation."""
    assert _parse_line("a=b=c") == ("a", "b=c")


# ---------------------------------------------------------------------------
# extract_xunity
# ---------------------------------------------------------------------------


def _setup_xunity(tmp_path: Path, files: dict[str, str]) -> Path:
    text_dir = tmp_path / "Translation" / "vi" / "Text"
    text_dir.mkdir(parents=True)
    for name, content in files.items():
        (text_dir / name).write_text(content, encoding="utf-8")
    return tmp_path


def test_extract_xunity_basic(tmp_path):
    _setup_xunity(tmp_path, {
        "manual.txt": "こんにちは=\nシンプルリング=Simple Ring\n",
    })
    entries = extract_xunity(tmp_path)
    sources = sorted(e.source for e in entries)
    assert sources == ["こんにちは", "シンプルリング"]


def test_extract_xunity_skip_translated(tmp_path):
    _setup_xunity(tmp_path, {
        "manual.txt": "こんにちは=\nシンプルリング=Simple Ring\n",
    })
    entries = extract_xunity(tmp_path, skip_translated=True)
    sources = sorted(e.source for e in entries)
    assert sources == ["こんにちは"]


def test_extract_xunity_skips_directives_and_regex(tmp_path):
    _setup_xunity(tmp_path, {
        "rules.txt": '#set level 1,2,3\nr:"^foo$"=bar\nsr:"^baz$"=qux\nrealtext=\n',
    })
    entries = extract_xunity(tmp_path)
    assert [e.source for e in entries] == ["realtext"]


def test_extract_xunity_skips_resizer_files(tmp_path):
    _setup_xunity(tmp_path, {
        "_resizer.txt": "Foo/Bar=ChangeFontSize(10)\n",
        "main.txt": "hello=\n",
    })
    entries = extract_xunity(tmp_path)
    assert [e.source for e in entries] == ["hello"]


def test_extract_xunity_dedupes_within_same_file(tmp_path):
    _setup_xunity(tmp_path, {
        "a.txt": "hello=\nhello=\n",
    })
    entries = extract_xunity(tmp_path)
    assert len(entries) == 1


def test_extract_xunity_only_scans_language_text_dirs(tmp_path):
    (tmp_path / "Translation" / "vi" / "Text").mkdir(parents=True)
    (tmp_path / "Translation" / "config.txt").write_text("bad=BAD\n", encoding="utf-8")
    (tmp_path / "Translation" / "vi" / "Other").mkdir(parents=True)
    (tmp_path / "Translation" / "vi" / "Other" / "other.txt").write_text("bad2=BAD\n", encoding="utf-8")
    (tmp_path / "Translation" / "vi" / "Text" / "main.txt").write_text("good=\n", encoding="utf-8")

    entries = extract_xunity(tmp_path)

    assert [e.source for e in entries] == ["good"]


# ---------------------------------------------------------------------------
# apply_xunity
# ---------------------------------------------------------------------------


def test_apply_xunity_writes_translations_back(tmp_path):
    _setup_xunity(tmp_path, {
        "main.txt": "こんにちは=\n#set level 1\nr:\"^x$\"=y\nfoo=existing\n",
    })
    text_dir = tmp_path / "Translation" / "vi" / "Text"
    main_file = text_dir / "main.txt"
    out_dir = tmp_path / "out"

    results = [
        TranslationResult(main_file, "こんにちは", "こんにちは", "Xin chào"),
        TranslationResult(main_file, "foo", "foo", "fú"),
    ]
    apply_xunity(results, out_dir)

    written = (out_dir / "vi" / "Text" / "main.txt").read_text(encoding="utf-8")
    assert "こんにちは=Xin chào" in written
    assert "foo=fú" in written
    assert "#set level 1" in written
    assert 'r:"^x$"=y' in written


def test_apply_xunity_keeps_original_when_target_empty(tmp_path):
    _setup_xunity(tmp_path, {
        "main.txt": "hello=existing\n",
    })
    main_file = tmp_path / "Translation" / "vi" / "Text" / "main.txt"
    out_dir = tmp_path / "out"

    results = [TranslationResult(main_file, "hello", "hello", "")]
    apply_xunity(results, out_dir)

    written = (out_dir / "vi" / "Text" / "main.txt").read_text(encoding="utf-8")
    assert "hello=existing" in written


def test_apply_xunity_preserves_relative_paths_to_avoid_collisions(tmp_path):
    vi_dir = tmp_path / "Translation" / "vi" / "Text"
    en_dir = tmp_path / "Translation" / "en" / "Text"
    vi_dir.mkdir(parents=True)
    en_dir.mkdir(parents=True)
    vi_file = vi_dir / "main.txt"
    en_file = en_dir / "main.txt"
    vi_file.write_text("hello=\n", encoding="utf-8")
    en_file.write_text("hello=\n", encoding="utf-8")
    out_dir = tmp_path / "out"

    apply_xunity([
        TranslationResult(vi_file, "hello", "hello", "xin chào"),
        TranslationResult(en_file, "hello", "hello", "hello there"),
    ], out_dir)

    assert (out_dir / "vi" / "Text" / "main.txt").read_text(encoding="utf-8") == "hello=xin chào\n"
    assert (out_dir / "en" / "Text" / "main.txt").read_text(encoding="utf-8") == "hello=hello there\n"
