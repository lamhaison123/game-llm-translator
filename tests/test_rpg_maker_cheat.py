from __future__ import annotations

import io
import tarfile
import zipfile

import pytest

from game_llm_translator import rpg_maker_cheat as cheat


def _sha256(path):
    return cheat._sha256(path)


def _write_zip(path, files: dict[str, str]) -> None:
    with zipfile.ZipFile(path, "w") as archive:
        for name, content in files.items():
            archive.writestr(name, content)


def _write_tar_gz(path, files: dict[str, str]) -> None:
    with tarfile.open(path, "w:gz") as archive:
        for name, content in files.items():
            data = content.encode("utf-8")
            info = tarfile.TarInfo(name)
            info.size = len(data)
            archive.addfile(info, io.BytesIO(data))


def test_detect_cheat_engine_detected_mz_overrides_user_mv(tmp_path):
    """When engine is definitively detected as MZ (rmmz_core.js), detection wins over user selection."""
    (tmp_path / "js").mkdir()
    (tmp_path / "js" / "rmmz_core.js").write_text("")

    assert cheat.detect_cheat_engine(tmp_path, "rpg-maker-mv") == "mz"


def test_detect_cheat_engine_user_mv_overrides_ambiguous_mv_mz(tmp_path):
    """When detection is ambiguous (mv-mz), user selection should win."""
    (tmp_path / "data").mkdir()

    assert cheat.detect_cheat_engine(tmp_path, "rpg-maker-mv") == "mv"


def test_detect_cheat_engine_user_mz_overrides_ambiguous_mv_mz(tmp_path):
    (tmp_path / "data").mkdir()

    assert cheat.detect_cheat_engine(tmp_path, "rpg-maker-mz") == "mz"


def test_detect_cheat_engine_rejects_unknown_when_detection_and_selection_missing(tmp_path, monkeypatch):
    monkeypatch.setattr(cheat, "normalize_gui_game_type", lambda value: value or "")

    with pytest.raises(ValueError, match="RPG Maker MV/MZ game not detected"):
        cheat.detect_cheat_engine(tmp_path, None)


def test_safe_zip_members_rejects_path_traversal(tmp_path):
    archive_path = tmp_path / "bad.zip"
    _write_zip(archive_path, {"../evil.js": "bad"})

    with pytest.raises(ValueError, match="Unsafe archive entry"):
        cheat._safe_zip_members(archive_path)


def test_safe_tar_members_rejects_path_traversal(tmp_path):
    archive_path = tmp_path / "bad.tar.gz"
    _write_tar_gz(archive_path, {"nested/../../evil.js": "bad"})

    with pytest.raises(ValueError, match="Unsafe archive entry"):
        cheat._safe_tar_members(archive_path)


def test_download_cheat_release_reuses_valid_cached_archive(tmp_path, monkeypatch):
    cached = tmp_path / "v1" / "rpg-mz-cheat.tar.gz"
    cached.parent.mkdir()
    _write_tar_gz(cached, {"js/plugins/cheat.js": "plugin"})

    class ReleaseResponse:
        def raise_for_status(self):
            pass

        def json(self):
            return {
                "tag_name": "v1",
                "assets": [{"name": "rpg-mz-cheat.tar.gz", "browser_download_url": "https://example.invalid/mz"}],
            }

    calls = []

    def fake_get(url, **kwargs):
        calls.append((url, kwargs))
        return ReleaseResponse()

    monkeypatch.setattr(cheat.requests, "get", fake_get)

    assert cheat.download_cheat_release("mz", tmp_path, prefer_bundled=False) == (cached, "v1", "rpg-mz-cheat.tar.gz")
    assert len(calls) == 1


def test_apply_and_remove_cheat_mz_restores_backup_and_deletes_created_files(tmp_path, monkeypatch):
    game_dir = tmp_path / "game"
    (game_dir / "js" / "plugins").mkdir(parents=True)
    existing = game_dir / "js" / "main.js"
    existing.write_text("original", encoding="utf-8")
    archive_path = tmp_path / "rpg-mz-cheat.tar.gz"
    _write_tar_gz(archive_path, {"js/main.js": "patched", "cheat/ui.js": "new"})
    monkeypatch.setattr(cheat, "download_cheat_release", lambda engine: (archive_path, "v1", archive_path.name))

    manifest = cheat.apply_cheat(game_dir, "mz")

    assert existing.read_text(encoding="utf-8") == "patched"
    assert (game_dir / "cheat" / "ui.js").read_text(encoding="utf-8") == "new"
    assert len(manifest.files) == 2
    assert cheat.cheat_manifest_path(game_dir).exists()
    overwritten = next(record for record in manifest.files if record.overwrote)
    assert overwritten.source_sha256 == _sha256(tmp_path / "game" / "translator_work" / "cheat_backups" / next((tmp_path / "game" / "translator_work" / "cheat_backups").iterdir()).name / "js" / "main.js")

    removed_manifest = cheat.remove_cheat(game_dir)

    assert removed_manifest.release_tag == "v1"
    assert existing.read_text(encoding="utf-8") == "original"
    assert not (game_dir / "cheat" / "ui.js").exists()
    assert not cheat.cheat_manifest_path(game_dir).exists()
    assert list((game_dir / "translator_work").glob("cheat_manifest_removed_*.json"))


def test_remove_cheat_skips_modified_created_file(tmp_path, monkeypatch):
    game_dir = tmp_path / "game"
    game_dir.mkdir()
    archive_path = tmp_path / "rpg-mz-cheat.tar.gz"
    _write_tar_gz(archive_path, {"cheat/ui.js": "new"})
    monkeypatch.setattr(cheat, "download_cheat_release", lambda engine: (archive_path, "v1", archive_path.name))
    cheat.apply_cheat(game_dir, "mz")
    created = game_dir / "cheat" / "ui.js"
    created.write_text("user changed", encoding="utf-8")

    cheat.remove_cheat(game_dir)

    assert created.read_text(encoding="utf-8") == "user changed"


def test_cheat_status_counts_missing_and_modified_files(tmp_path):
    game_dir = tmp_path / "game"
    game_dir.mkdir()
    ok = game_dir / "ok.js"
    changed = game_dir / "changed.js"
    ok.write_text("ok", encoding="utf-8")
    changed.write_text("old", encoding="utf-8")
    manifest = cheat.CheatManifest(
        schema_version=1,
        plugin=cheat.CHEAT_PLUGIN_NAME,
        plugin_source=cheat.CHEAT_REPO_URL,
        plugin_license="MIT",
        release_tag="v1",
        asset_name="asset.tar.gz",
        engine="mz",
        installed_at="now",
        game_dir=str(game_dir),
        destination_root=".",
        backup_dir=str(game_dir / "translator_work" / "cheat_backups" / "backup"),
        files=[
            cheat.CheatFileRecord("ok.js", None, None, _sha256(ok), True, False),
            cheat.CheatFileRecord("changed.js", None, None, _sha256(changed), True, False),
            cheat.CheatFileRecord("missing.js", None, None, "missing", True, False),
        ],
        created_dirs=[],
    )
    cheat._write_manifest(cheat.cheat_manifest_path(game_dir), manifest)
    changed.write_text("new", encoding="utf-8")

    status = cheat.cheat_status(game_dir)

    assert status.installed is True
    assert status.file_count == 3
    assert status.missing_count == 1
    assert status.modified_count == 1


def test_find_bundled_archive_returns_existing_path_when_vendored():
    from game_llm_translator.rpg_maker_cheat import _find_bundled_archive
    mv = _find_bundled_archive("mv")
    if mv is not None:
        assert mv.exists()
        assert "mv" in mv.name.lower()


def test_bundled_tag_extracts_version():
    from game_llm_translator.rpg_maker_cheat import _bundled_tag
    assert _bundled_tag("rpg-mv-cheat-1.0.3-core.tar.gz") == "v1.0.3"
    assert _bundled_tag("rpg-mz-cheat-2.5.0-core.tar.gz") == "v2.5.0"
    assert _bundled_tag("anything-no-version.tar.gz") == "bundled"
