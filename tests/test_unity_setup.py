"""Tests for unity_setup: detect_unity_bare, install/uninstall manifest logic, xunity_install_status."""
from __future__ import annotations

import zipfile
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

from game_llm_translator.unity_setup import (
    detect_unity_bare,
    xunity_install_status,
    xunity_manifest_path,
    install_xunity,
    uninstall_xunity,
    _write_manifest,
    _read_manifest,
    XUnityManifest,
    XUnityFileRecord,
)


# ---------------------------------------------------------------------------
# detect_unity_bare
# ---------------------------------------------------------------------------

def test_detect_unity_bare_with_unityplayer(tmp_path):
    (tmp_path / "UnityPlayer.dll").write_bytes(b"fake")
    (tmp_path / "Game_Data").mkdir()
    assert detect_unity_bare(tmp_path) is True


def test_detect_unity_bare_linux(tmp_path):
    (tmp_path / "UnityPlayer.so").write_bytes(b"fake")
    assert detect_unity_bare(tmp_path) is True


def test_detect_unity_bare_false_when_bepinex_present(tmp_path):
    (tmp_path / "UnityPlayer.dll").write_bytes(b"fake")
    (tmp_path / "BepInEx").mkdir()
    assert detect_unity_bare(tmp_path) is False


def test_detect_unity_bare_false_when_no_unityplayer(tmp_path):
    assert detect_unity_bare(tmp_path) is False


def test_detect_unity_bare_false_for_rpgmaker(tmp_path):
    (tmp_path / "www").mkdir()
    (tmp_path / "Game.exe").write_bytes(b"fake")
    assert detect_unity_bare(tmp_path) is False


# ---------------------------------------------------------------------------
# xunity_install_status
# ---------------------------------------------------------------------------

def test_xunity_install_status_not_unity(tmp_path):
    s = xunity_install_status(tmp_path)
    assert s["status"] == "not_unity"


def test_xunity_install_status_not_installed(tmp_path):
    (tmp_path / "UnityPlayer.dll").write_bytes(b"fake")
    s = xunity_install_status(tmp_path)
    assert s["status"] == "not_installed"


def test_xunity_install_status_manual_bepinex(tmp_path):
    (tmp_path / "UnityPlayer.dll").write_bytes(b"fake")
    (tmp_path / "BepInEx").mkdir()
    s = xunity_install_status(tmp_path)
    assert s["status"] == "manual"


def test_xunity_install_status_installed(tmp_path):
    (tmp_path / "UnityPlayer.dll").write_bytes(b"fake")
    manifest = XUnityManifest(
        schema_version=1,
        bepinex_tag="v5.4.23",
        xunity_tag="v5.6.1",
        installed_at="2026-01-01T00:00:00+00:00",
        game_dir=str(tmp_path),
        files=[],
        created_dirs=[],
    )
    _write_manifest(xunity_manifest_path(tmp_path), manifest)
    s = xunity_install_status(tmp_path)
    assert s["status"] == "installed"
    assert s["bepinex_tag"] == "v5.4.23"
    assert s["xunity_tag"] == "v5.6.1"


def test_xunity_install_status_installed_with_translation_dir(tmp_path):
    (tmp_path / "UnityPlayer.dll").write_bytes(b"fake")
    (tmp_path / "Translation").mkdir()
    manifest = XUnityManifest(
        schema_version=1, bepinex_tag="v5.4", xunity_tag="v5.6",
        installed_at="2026-01-01T00:00:00+00:00",
        game_dir=str(tmp_path), files=[], created_dirs=[],
    )
    _write_manifest(xunity_manifest_path(tmp_path), manifest)
    s = xunity_install_status(tmp_path)
    assert s["has_translation_dir"] is True


# ---------------------------------------------------------------------------
# Manifest round-trip
# ---------------------------------------------------------------------------

def test_manifest_roundtrip(tmp_path):
    manifest = XUnityManifest(
        schema_version=1,
        bepinex_tag="v5.4.23",
        xunity_tag="v5.6.1",
        installed_at="2026-01-01T00:00:00+00:00",
        game_dir=str(tmp_path),
        files=[XUnityFileRecord("BepInEx/core.dll", "abc123", created=True)],
        created_dirs=["BepInEx"],
    )
    path = tmp_path / "translator_work" / "xunity_manifest.json"
    _write_manifest(path, manifest)
    loaded = _read_manifest(path)
    assert loaded.bepinex_tag == "v5.4.23"
    assert loaded.xunity_tag == "v5.6.1"
    assert len(loaded.files) == 1
    assert loaded.files[0].relative_path == "BepInEx/core.dll"


# ---------------------------------------------------------------------------
# install_xunity (mocked HTTP)
# ---------------------------------------------------------------------------

def _make_zip(tmp_path: Path, name: str, files: dict[str, bytes]) -> Path:
    zp = tmp_path / name
    with zipfile.ZipFile(zp, "w") as zf:
        for fname, data in files.items():
            zf.writestr(fname, data)
    return zp


def test_install_xunity_mocked(tmp_path):
    (tmp_path / "UnityPlayer.dll").write_bytes(b"fake")
    cache = tmp_path / "cache"
    cache.mkdir()

    bepinex_zip = _make_zip(cache, "BepInEx.zip", {"winhttp.dll": b"bep", "BepInEx/core.dll": b"core"})
    xunity_zip = _make_zip(cache, "XUnity.zip", {"BepInEx/plugins/XUnity.dll": b"xu"})

    def fake_latest_asset(api_url, name_filter):
        if "BepInEx" in api_url:
            return ("http://fake/bepinex.zip", "v5.4.23")
        return ("http://fake/xunity.zip", "v5.6.1")

    def fake_download(url, dest, progress=None):
        if "bepinex" in url:
            import shutil
            shutil.copy2(bepinex_zip, dest)
        else:
            import shutil
            shutil.copy2(xunity_zip, dest)

    with patch("game_llm_translator.unity_setup._cache_dir", return_value=cache), \
         patch("game_llm_translator.unity_setup._find_bundled_bepinex", return_value=None), \
         patch("game_llm_translator.unity_setup._find_bundled_xunity", return_value=None), \
         patch("game_llm_translator.unity_setup._latest_release_asset", side_effect=fake_latest_asset), \
         patch("game_llm_translator.unity_setup._download_file", side_effect=fake_download):
        manifest = install_xunity(tmp_path, target_lang="vi")

    assert manifest.bepinex_tag == "v5.4.23"
    assert manifest.xunity_tag == "v5.6.1"
    assert (tmp_path / "winhttp.dll").exists()
    assert (tmp_path / "BepInEx" / "core.dll").exists()
    assert (tmp_path / "BepInEx" / "plugins" / "XUnity.dll").exists()
    assert xunity_manifest_path(tmp_path).exists()


def test_install_xunity_uses_bundled_when_available(tmp_path):
    """When bundled zips exist, no HTTP calls should be made."""
    (tmp_path / "UnityPlayer.dll").write_bytes(b"fake")
    cache = tmp_path / "cache"
    cache.mkdir()

    bepinex_zip = _make_zip(cache, "BepInEx_win_x64_5.4.23.zip", {"winhttp.dll": b"bep"})
    xunity_zip = _make_zip(cache, "XUnity.AutoTranslator-BepInEx-5.6.1.zip", {"BepInEx/plugins/XUnity.dll": b"xu"})

    with patch("game_llm_translator.unity_setup._find_bundled_bepinex", return_value=bepinex_zip), \
         patch("game_llm_translator.unity_setup._find_bundled_xunity", return_value=xunity_zip), \
         patch("game_llm_translator.unity_setup._latest_release_asset") as mock_online, \
         patch("game_llm_translator.unity_setup._download_file") as mock_dl:
        manifest = install_xunity(tmp_path, target_lang="vi")

    mock_online.assert_not_called()
    mock_dl.assert_not_called()
    assert manifest.bepinex_tag == "v5.4.23"
    assert manifest.xunity_tag == "v5.6.1"
    assert (tmp_path / "winhttp.dll").exists()


def test_tag_from_zip_name():
    from game_llm_translator.unity_setup import _tag_from_zip_name
    assert _tag_from_zip_name("BepInEx_win_x64_5.4.23.5.zip") == "v5.4.23.5"
    assert _tag_from_zip_name("XUnity.AutoTranslator-BepInEx-5.6.1.zip") == "v5.6.1"
    assert _tag_from_zip_name("unknown.zip") == "bundled"


def test_install_xunity_raises_if_manifest_exists(tmp_path):
    (tmp_path / "UnityPlayer.dll").write_bytes(b"fake")
    manifest = XUnityManifest(1, "v1", "v1", "2026-01-01", str(tmp_path), [], [])
    _write_manifest(xunity_manifest_path(tmp_path), manifest)
    with pytest.raises(ValueError, match="already installed"):
        install_xunity(tmp_path)


# ---------------------------------------------------------------------------
# uninstall_xunity
# ---------------------------------------------------------------------------

def test_uninstall_xunity(tmp_path):
    (tmp_path / "UnityPlayer.dll").write_bytes(b"fake")
    target = tmp_path / "winhttp.dll"
    target.write_bytes(b"bep")
    import hashlib
    sha = hashlib.sha256(b"bep").hexdigest()

    manifest = XUnityManifest(
        schema_version=1, bepinex_tag="v5", xunity_tag="v5",
        installed_at="2026-01-01T00:00:00+00:00",
        game_dir=str(tmp_path),
        files=[XUnityFileRecord("winhttp.dll", sha, created=True)],
        created_dirs=[],
    )
    _write_manifest(xunity_manifest_path(tmp_path), manifest)
    uninstall_xunity(tmp_path)
    assert not target.exists()
    assert not xunity_manifest_path(tmp_path).exists()


def test_uninstall_xunity_raises_if_no_manifest(tmp_path):
    with pytest.raises(ValueError, match="No XUnity install manifest"):
        uninstall_xunity(tmp_path)
