"""Auto-install BepInEx + XUnity.AutoTranslator into a bare Unity game.

Workflow:
1. detect_unity_bare(game_dir) -> bool   – game has UnityPlayer.dll but no BepInEx yet
2. install_xunity(game_dir, progress)    – download BepInEx + XUnity, extract into game folder
3. uninstall_xunity(game_dir, progress)  – restore using manifest
"""
from __future__ import annotations

import hashlib
import json
import platform
import shutil
import tempfile
import zipfile
from collections.abc import Callable
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import requests

from .app_config import app_data_dir

BEPINEX_REPO_API = "https://api.github.com/repos/BepInEx/BepInEx/releases/latest"
XUNITY_REPO_API = "https://api.github.com/repos/bbepis/XUnity.AutoTranslator/releases/latest"

XUNITY_LANG_MAP: dict[str, str] = {
    "vietnamese": "vi",
    "english": "en",
    "chinese": "zh-CN",
    "chinese simplified": "zh-CN",
    "chinese traditional": "zh-TW",
    "japanese": "ja",
    "korean": "ko",
    "thai": "th",
    "russian": "ru",
    "french": "fr",
    "german": "de",
    "spanish": "es",
    "portuguese": "pt",
    "indonesian": "id",
    "arabic": "ar",
    "turkish": "tr",
    "italian": "it",
    "polish": "pl",
}

XUNITY_ASSET_NAME = "XUnity.AutoTranslator-BepInEx-"
BEPINEX_WIN_ASSET = "BepInEx_win_x64_"
BEPINEX_LINUX_ASSET = "BepInEx_linux_x64_"

MANIFEST_FILENAME = "xunity_manifest.json"


# ---------------------------------------------------------------------------
# Detection
# ---------------------------------------------------------------------------

def detect_unity_bare(game_dir: Path) -> bool:
    """Return True if this looks like a Unity game that does NOT yet have BepInEx installed."""
    game_dir = Path(game_dir)
    has_unity = (game_dir / "UnityPlayer.dll").exists() or (game_dir / "UnityPlayer.so").exists()
    if not has_unity:
        return False
    has_bepinex = (game_dir / "BepInEx").is_dir()
    return not has_bepinex


def xunity_manifest_path(game_dir: Path) -> Path:
    return game_dir / "translator_work" / MANIFEST_FILENAME


# ---------------------------------------------------------------------------
# Manifest
# ---------------------------------------------------------------------------

@dataclass(slots=True)
class XUnityFileRecord:
    relative_path: str
    installed_sha256: str
    created: bool


@dataclass(slots=True)
class XUnityManifest:
    schema_version: int
    bepinex_tag: str
    xunity_tag: str
    installed_at: str
    game_dir: str
    files: list[XUnityFileRecord]
    created_dirs: list[str]


def _write_manifest(path: Path, manifest: XUnityManifest) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    data = asdict(manifest)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)


def _read_manifest(path: Path) -> XUnityManifest:
    data = json.loads(path.read_text(encoding="utf-8"))
    files = [XUnityFileRecord(**f) for f in data.get("files", [])]
    return XUnityManifest(
        schema_version=int(data.get("schema_version", 1)),
        bepinex_tag=str(data.get("bepinex_tag", "")),
        xunity_tag=str(data.get("xunity_tag", "")),
        installed_at=str(data.get("installed_at", "")),
        game_dir=str(data.get("game_dir", "")),
        files=files,
        created_dirs=[str(d) for d in data.get("created_dirs", [])],
    )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fp:
        for chunk in iter(lambda: fp.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _cache_dir() -> Path:
    path = app_data_dir() / "xunity_cache"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _is_windows() -> bool:
    return platform.system() == "Windows"


def _bundled_xunity_dir() -> Path:
    """Return path to vendored xunity archives shipped with the package.

    When running as a PyInstaller bundle, files added via --add-data live under sys._MEIPASS.
    Otherwise they're at <repo>/vendor/xunity/ relative to the source tree.
    """
    import sys
    bundle_dir = getattr(sys, "_MEIPASS", None)
    if bundle_dir:
        return Path(bundle_dir) / "vendor" / "xunity"
    return Path(__file__).resolve().parent.parent.parent / "vendor" / "xunity"


def _tag_from_zip_name(name: str) -> str:
    """Extract version tag from filenames.

    BepInEx_win_x64_5.4.23.5.zip -> v5.4.23.5
    XUnity.AutoTranslator-BepInEx-5.6.1.zip -> v5.6.1
    """
    stem = name.replace(".zip", "")
    for part in reversed(stem.replace("-", "_").split("_")):
        if part and part[0].isdigit() and "." in part:
            return f"v{part}"
    return "bundled"


def _find_bundled_bepinex() -> Path | None:
    """Return bundled BepInEx zip for the current platform, or None."""
    d = _bundled_xunity_dir()
    if not d.exists():
        return None
    prefix = "BepInEx_win_x64_" if _is_windows() else "BepInEx_linux_x64_"
    for f in d.glob("*.zip"):
        if f.name.startswith(prefix):
            return f
    return None


def _find_bundled_xunity() -> Path | None:
    """Return bundled XUnity (Mono) zip, or None."""
    d = _bundled_xunity_dir()
    if not d.exists():
        return None
    for f in d.glob("*.zip"):
        if "XUnity.AutoTranslator-BepInEx-" in f.name and "IL2CPP" not in f.name:
            return f
    return None


def _validate_zip(path: Path) -> bool:
    try:
        with zipfile.ZipFile(path) as zf:
            zf.namelist()
        return True
    except Exception:
        return False


def _download_file(url: str, dest: Path, progress: Callable[[str], None] | None = None) -> None:
    if dest.exists() and dest.stat().st_size > 0:
        return
    if progress:
        progress(f"Downloading {dest.name}...")
    tmp = dest.with_suffix(dest.suffix + ".tmp")
    with requests.get(url, stream=True, timeout=120) as r:
        r.raise_for_status()
        with tmp.open("wb") as fp:
            for chunk in r.iter_content(chunk_size=1024 * 1024):
                if chunk:
                    fp.write(chunk)
    tmp.replace(dest)


def _latest_release_asset(api_url: str, name_filter: str) -> tuple[str, str]:
    """Return (download_url, tag_name) for the first asset whose name contains name_filter."""
    r = requests.get(api_url, timeout=30)
    r.raise_for_status()
    release = r.json()
    tag = str(release.get("tag_name", "latest"))
    for asset in release.get("assets", []):
        name = str(asset.get("name", ""))
        if name_filter in name and name.endswith(".zip"):
            return str(asset["browser_download_url"]), tag
    raise ValueError(f"No release asset matching '{name_filter}' found in {api_url}")


def _safe_zip_extract(zip_path: Path, dest_dir: Path) -> list[Path]:
    """Extract zip safely (no path traversal), return list of extracted files."""
    extracted: list[Path] = []
    with zipfile.ZipFile(zip_path) as zf:
        for info in zf.infolist():
            if info.is_dir():
                continue
            name = info.filename.replace("\\", "/")
            target = dest_dir / name
            if not target.resolve().is_relative_to(dest_dir.resolve()):
                raise ValueError(f"Unsafe zip entry: {info.filename}")
            target.parent.mkdir(parents=True, exist_ok=True)
            with zf.open(info) as src, target.open("wb") as dst:
                shutil.copyfileobj(src, dst)
            extracted.append(target)
    return extracted


# ---------------------------------------------------------------------------
# Install / Uninstall
# ---------------------------------------------------------------------------

def install_xunity(
    game_dir: Path,
    progress: Callable[[str], None] | None = None,
    target_lang: str = "ja",
) -> XUnityManifest:
    """Download and install BepInEx + XUnity.AutoTranslator into the Unity game folder.

    Records every installed file in translator_work/xunity_manifest.json so it can
    be cleanly uninstalled later.
    """
    game_dir = Path(game_dir)
    manifest_path = xunity_manifest_path(game_dir)
    if manifest_path.exists():
        raise ValueError(f"XUnity already installed (manifest exists): {manifest_path}")

    cache = _cache_dir()
    is_win = _is_windows()

    # --- Resolve BepInEx: bundled > cached > online ---
    bepinex_zip: Path
    bepinex_tag: str
    bundled_bep = _find_bundled_bepinex()
    if bundled_bep is not None and _validate_zip(bundled_bep):
        bepinex_zip = bundled_bep
        bepinex_tag = _tag_from_zip_name(bundled_bep.name)
        if progress:
            progress(f"BepInEx: using bundled {bundled_bep.name}")
    else:
        bepinex_filter = BEPINEX_WIN_ASSET if is_win else BEPINEX_LINUX_ASSET
        if progress:
            progress("BepInEx: fetching latest release from GitHub...")
        bepinex_url, bepinex_tag = _latest_release_asset(BEPINEX_REPO_API, bepinex_filter)
        bepinex_zip = cache / f"BepInEx_{bepinex_tag}_{'win' if is_win else 'linux'}_x64.zip"
        _download_file(bepinex_url, bepinex_zip, progress)

    # --- Resolve XUnity: bundled > cached > online ---
    xunity_zip: Path
    xunity_tag: str
    bundled_xu = _find_bundled_xunity()
    if bundled_xu is not None and _validate_zip(bundled_xu):
        xunity_zip = bundled_xu
        xunity_tag = _tag_from_zip_name(bundled_xu.name)
        if progress:
            progress(f"XUnity: using bundled {bundled_xu.name}")
    else:
        if progress:
            progress("XUnity: fetching latest release from GitHub...")
        xunity_url, xunity_tag = _latest_release_asset(XUNITY_REPO_API, XUNITY_ASSET_NAME)
        xunity_zip = cache / f"XUnity_{xunity_tag}.zip"
        _download_file(xunity_url, xunity_zip, progress)

    records: list[XUnityFileRecord] = []
    created_dirs: set[str] = set()

    def _install_zip(zip_path: Path, label: str) -> None:
        if progress:
            progress(f"Installing {label}...")
        with tempfile.TemporaryDirectory(prefix="game_translator_xunity_") as tmp:
            staging = Path(tmp)
            extracted = _safe_zip_extract(zip_path, staging)
            for src in extracted:
                rel = src.relative_to(staging)
                dst = game_dir / rel
                created = not dst.exists()
                dst.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(src, dst)
                records.append(XUnityFileRecord(
                    relative_path=rel.as_posix(),
                    installed_sha256=_sha256(dst),
                    created=created,
                ))
                for parent in dst.parents:
                    if parent == game_dir:
                        break
                    if game_dir in parent.parents:
                        created_dirs.add(parent.relative_to(game_dir).as_posix())

    _install_zip(bepinex_zip, "BepInEx")
    _install_zip(xunity_zip, "XUnity.AutoTranslator")

    # --- Write XUnity config so it sources text automatically ---
    _write_xunity_config(game_dir, target_lang, progress)

    manifest = XUnityManifest(
        schema_version=1,
        bepinex_tag=bepinex_tag,
        xunity_tag=xunity_tag,
        installed_at=_now_iso(),
        game_dir=str(game_dir),
        files=records,
        created_dirs=sorted(created_dirs, key=lambda d: d.count("/"), reverse=True),
    )
    _write_manifest(manifest_path, manifest)
    if progress:
        progress(
            f"BepInEx {bepinex_tag} + XUnity {xunity_tag} installed.\n"
            f"Run the game ONCE to collect text, then come back to translate.\n"
            f"Manifest -> {manifest_path}"
        )
    return manifest


def resolve_xunity_lang(lang: str) -> str:
    """Convert a full language name or ISO code to the XUnity-compatible ISO code.

    Examples: 'Vietnamese' -> 'vi', 'vi' -> 'vi', 'Chinese Simplified' -> 'zh-CN'
    Unknown values are returned as-is (may already be a valid code like 'zh-TW').
    """
    return XUNITY_LANG_MAP.get(lang.strip().lower(), lang.strip())


def _write_xunity_config(game_dir: Path, target_lang: str, progress: Callable[[str], None] | None = None) -> None:
    """Write XUnity AutoTranslatorConfig.ini with correct language ISO code.

    Always overwrites so re-running install_xunity with a different language works.
    """
    config_path = game_dir / "BepInEx" / "config" / "AutoTranslatorConfig.ini"
    config_path.parent.mkdir(parents=True, exist_ok=True)
    lang_code = resolve_xunity_lang(target_lang or "vi")
    config_content = f"""[General]
Language={lang_code}
FromLanguage=ja
EnableTranslation=True

[Service]
Endpoint=GoogleTranslate

[Behaviour]
MaxCharactersPerTranslation=200
IgnoreWhitespaceInDialogue=True
IgnoreWhitespaceInNGUI=True
OutputUntranslatableText=True

[Files]
Directory=Translation\\{{Lang}}\\Text
OutputFile=Translation\\{{Lang}}\\Text\\_AutoGeneratedTranslations.txt
"""
    config_path.write_text(config_content, encoding="utf-8")
    # Pre-create the Translation dir so XUnity can write immediately
    trans_dir = game_dir / "BepInEx" / "Translation" / lang_code / "Text"
    trans_dir.mkdir(parents=True, exist_ok=True)
    if progress:
        progress(f"XUnity config: Language={lang_code} -> {config_path}")


def uninstall_xunity(
    game_dir: Path,
    progress: Callable[[str], None] | None = None,
) -> XUnityManifest:
    """Remove BepInEx + XUnity files recorded in the manifest."""
    game_dir = Path(game_dir)
    manifest_path = xunity_manifest_path(game_dir)
    if not manifest_path.exists():
        raise ValueError(f"No XUnity install manifest found: {manifest_path}")
    manifest = _read_manifest(manifest_path)

    deleted = 0
    skipped = 0
    for record in manifest.files:
        path = game_dir / record.relative_path
        if not path.exists():
            skipped += 1
            continue
        if _sha256(path) != record.installed_sha256:
            skipped += 1
            continue
        path.unlink()
        deleted += 1

    for rel_dir in sorted(manifest.created_dirs, key=lambda d: d.count("/"), reverse=True):
        directory = game_dir / rel_dir
        try:
            directory.rmdir()
        except OSError:
            pass

    removed_path = manifest_path.with_name(
        f"xunity_manifest_removed_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
    )
    manifest_path.replace(removed_path)
    if progress:
        progress(f"XUnity removed: deleted {deleted}, skipped {skipped}. Old manifest -> {removed_path}")
    return manifest


def xunity_install_status(game_dir: Path) -> dict[str, Any]:
    """Return install status dict for GUI display."""
    game_dir = Path(game_dir)
    manifest_path = xunity_manifest_path(game_dir)
    bare = detect_unity_bare(game_dir)
    has_bepinex = (game_dir / "BepInEx").is_dir()
    has_manifest = manifest_path.exists()
    has_translation_dir = (
        (game_dir / "Translation").is_dir()
        or (game_dir / "BepInEx" / "Translation").is_dir()
    )
    if has_manifest:
        try:
            m = _read_manifest(manifest_path)
            return {
                "status": "installed",
                "bepinex_tag": m.bepinex_tag,
                "xunity_tag": m.xunity_tag,
                "installed_at": m.installed_at,
                "has_translation_dir": has_translation_dir,
                "manifest_path": str(manifest_path),
            }
        except Exception as exc:
            return {"status": "error", "error": str(exc)}
    if has_bepinex:
        return {
            "status": "manual",
            "has_translation_dir": has_translation_dir,
            "note": "BepInEx detected but not installed by this tool",
        }
    if bare:
        return {"status": "not_installed", "has_translation_dir": False}
    return {"status": "not_unity"}
