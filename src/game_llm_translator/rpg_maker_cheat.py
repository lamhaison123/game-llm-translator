from __future__ import annotations

import hashlib
import json
import shutil
import tempfile
import tarfile
import zipfile
from collections.abc import Callable
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal

import requests

from .app_config import app_data_dir
from .rpg_maker_common import detect_rpg_maker, normalize_gui_game_type

CHEAT_REPO = "paramonos/RPG-Maker-MV-MZ-Cheat-UI-Plugin"
CHEAT_REPO_URL = f"https://github.com/{CHEAT_REPO}"
CHEAT_RELEASE_API = f"https://api.github.com/repos/{CHEAT_REPO}/releases/latest"
CHEAT_PLUGIN_NAME = "RPG-Maker-MV-MZ-Cheat-UI-Plugin"


@dataclass(slots=True)
class CheatFileRecord:
    relative_path: str
    backup_path: str | None
    source_sha256: str | None
    installed_sha256: str
    created: bool
    overwrote: bool


@dataclass(slots=True)
class CheatManifest:
    schema_version: int
    plugin: str
    plugin_source: str
    plugin_license: str
    release_tag: str
    asset_name: str
    engine: str
    installed_at: str
    game_dir: str
    destination_root: str
    backup_dir: str
    files: list[CheatFileRecord]
    created_dirs: list[str]


@dataclass(slots=True)
class CheatStatus:
    installed: bool
    manifest_path: Path
    engine: str | None = None
    release_tag: str | None = None
    installed_at: str | None = None
    file_count: int = 0
    missing_count: int = 0
    modified_count: int = 0
    error: str | None = None


def cheat_manifest_path(game_dir: Path) -> Path:
    return game_dir / "translator_work" / "cheat_manifest.json"


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fp:
        for chunk in iter(lambda: fp.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _now_slug() -> str:
    return datetime.now().strftime("%Y%m%d_%H%M%S")


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _manifest_to_dict(manifest: CheatManifest) -> dict[str, Any]:
    return asdict(manifest)


def _manifest_from_dict(data: dict[str, Any]) -> CheatManifest:
    files = [CheatFileRecord(**item) for item in data.get("files", [])]
    return CheatManifest(
        schema_version=int(data.get("schema_version", 1)),
        plugin=str(data.get("plugin", CHEAT_PLUGIN_NAME)),
        plugin_source=str(data.get("plugin_source", CHEAT_REPO_URL)),
        plugin_license=str(data.get("plugin_license", "MIT")),
        release_tag=str(data.get("release_tag", "")),
        asset_name=str(data.get("asset_name", "")),
        engine=str(data.get("engine", "")),
        installed_at=str(data.get("installed_at", "")),
        game_dir=str(data.get("game_dir", "")),
        destination_root=str(data.get("destination_root", "")),
        backup_dir=str(data.get("backup_dir", "")),
        files=files,
        created_dirs=[str(item) for item in data.get("created_dirs", [])],
    )


def _write_manifest(path: Path, manifest: CheatManifest) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(_manifest_to_dict(manifest), ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)


def _read_manifest(path: Path) -> CheatManifest:
    return _manifest_from_dict(json.loads(path.read_text(encoding="utf-8")))


def detect_cheat_engine(game_dir: Path, selected_game_type: str | None = None) -> Literal["mv", "mz"]:
    selected = normalize_gui_game_type(selected_game_type)
    detected = detect_rpg_maker(game_dir)

    if selected == "unity-xunity":
        raise ValueError("Cheat plugin is only available for RPG Maker MV/MZ games, not Unity.")

    if detected == "mz" or detected == "mv":
        return detected

    if detected is not None and detected not in {"mv-mz"}:
        raise ValueError(
            f"Detected engine is {detected.upper()}, which is not supported by the cheat plugin. "
            "Only RPG Maker MV/MZ are supported."
        )

    if detected == "mv-mz":
        if selected_game_type in {"rpg-maker-mv", "mv"}:
            return "mv"
        return "mz"

    if selected == "rpg-maker-mz":
        return "mz"
    if selected == "rpg-maker-mv":
        return "mv"

    raise ValueError(f"RPG Maker MV/MZ game not detected: {game_dir}")


def cheat_status(game_dir: Path) -> CheatStatus:
    path = cheat_manifest_path(game_dir)
    if not path.exists():
        return CheatStatus(False, path)
    try:
        manifest = _read_manifest(path)
        missing = 0
        modified = 0
        for record in manifest.files:
            destination = _manifest_destination(game_dir, record.relative_path)
            if not destination.exists():
                missing += 1
            elif _sha256(destination) != record.installed_sha256:
                modified += 1
        return CheatStatus(True, path, manifest.engine, manifest.release_tag, manifest.installed_at, len(manifest.files), missing, modified)
    except Exception as exc:
        return CheatStatus(False, path, error=str(exc))


def _cache_dir() -> Path:
    path = app_data_dir() / "cheat_plugin"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _is_archive_name(name: str) -> bool:
    return name.endswith(".zip") or name.endswith(".tar.gz") or name.endswith(".tgz")


def _select_asset(release: dict[str, Any], engine: str) -> dict[str, Any]:
    assets = release.get("assets", [])
    candidates = []
    for asset in assets:
        name = str(asset.get("name", "")).lower()
        if not _is_archive_name(name):
            continue
        if engine == "mv" and "mv" in name and "mz" not in name:
            candidates.append(asset)
        elif engine == "mz" and "mz" in name:
            candidates.append(asset)
    if not candidates:
        raise ValueError(f"No {engine.upper()} cheat release archive found in latest GitHub release")
    return candidates[0]


def _validate_cached_archive(path: Path) -> bool:
    try:
        _safe_archive_members(path)
    except Exception:
        return False
    return True


def _bundled_cheat_dir() -> Path:
    """Return path to vendored cheat archives shipped with the package.

    When running as a PyInstaller bundle, files added via --add-data live under sys._MEIPASS.
    Otherwise they're at <repo>/vendor/cheat/ relative to the source tree.
    """
    import sys
    bundle_dir = getattr(sys, "_MEIPASS", None)
    if bundle_dir:
        return Path(bundle_dir) / "vendor" / "cheat"
    return Path(__file__).resolve().parent.parent.parent / "vendor" / "cheat"


def _find_bundled_archive(engine: str) -> Path | None:
    """Look for a bundled cheat archive matching the engine; return path or None."""
    bundled_dir = _bundled_cheat_dir()
    if not bundled_dir.exists():
        return None
    for archive in bundled_dir.glob("rpg-*-cheat-*-core.tar.gz"):
        name = archive.name.lower()
        if engine == "mv" and "rpg-mv-cheat" in name:
            return archive
        if engine == "mz" and "rpg-mz-cheat" in name:
            return archive
    return None


def _bundled_tag(archive_name: str) -> str:
    """Extract version tag from filename like 'rpg-mv-cheat-1.0.3-core.tar.gz' -> 'v1.0.3'."""
    parts = archive_name.split("-")
    for i, p in enumerate(parts):
        if p.replace(".", "").isdigit():
            return f"v{p}"
    return "bundled"


def download_cheat_release(engine: str, cache_dir: Path | None = None, prefer_bundled: bool = True) -> tuple[Path, str, str]:
    # Try bundled archive first - works offline, no GitHub rate limit
    if prefer_bundled:
        bundled = _find_bundled_archive(engine)
        if bundled is not None and _validate_cached_archive(bundled):
            return bundled, _bundled_tag(bundled.name), bundled.name

    cache_root = cache_dir or _cache_dir()
    response = requests.get(CHEAT_RELEASE_API, timeout=30)
    response.raise_for_status()
    release = response.json()
    tag = str(release.get("tag_name") or "latest")
    asset = _select_asset(release, engine)
    asset_name = str(asset["name"])
    target_dir = cache_root / tag
    target_dir.mkdir(parents=True, exist_ok=True)
    zip_path = target_dir / asset_name
    if zip_path.exists() and zip_path.stat().st_size > 0:
        if _validate_cached_archive(zip_path):
            return zip_path, tag, asset_name
        zip_path.unlink()
    download_url = str(asset.get("browser_download_url") or "")
    if not download_url:
        raise ValueError(f"Release asset has no download URL: {asset_name}")
    with requests.get(download_url, stream=True, timeout=60) as download:
        download.raise_for_status()
        tmp = zip_path.with_suffix(zip_path.suffix + ".tmp")
        with tmp.open("wb") as fp:
            for chunk in download.iter_content(chunk_size=1024 * 1024):
                if chunk:
                    fp.write(chunk)
        tmp.replace(zip_path)
    return zip_path, tag, asset_name


def _safe_archive_member(name: str) -> None:
    normalized = name.replace("\\", "/")
    path = Path(normalized)
    if path.is_absolute() or ".." in path.parts:
        raise ValueError(f"Unsafe archive entry: {name}")


def _safe_zip_members(zip_path: Path) -> list[str]:
    with zipfile.ZipFile(zip_path) as archive:
        members = []
        for info in archive.infolist():
            if info.is_dir():
                continue
            _safe_archive_member(info.filename)
            members.append(info.filename)
        return members


def _safe_tar_members(tar_path: Path) -> list[tarfile.TarInfo]:
    with tarfile.open(tar_path, "r:gz") as archive:
        members: list[tarfile.TarInfo] = []
        for member in archive.getmembers():
            if not member.isfile():
                continue
            _safe_archive_member(member.name)
            members.append(member)
        return members


def _safe_archive_members(path: Path) -> None:
    name = path.name.lower()
    if name.endswith(".zip"):
        _safe_zip_members(path)
        return
    if name.endswith(".tar.gz") or name.endswith(".tgz"):
        _safe_tar_members(path)
        return
    raise ValueError(f"Unsupported cheat release archive: {path.name}")


def _extract_archive(archive_path: Path, target_dir: Path) -> None:
    name = archive_path.name.lower()
    if name.endswith(".zip"):
        _safe_zip_members(archive_path)
        with zipfile.ZipFile(archive_path) as archive:
            archive.extractall(target_dir)
        return
    if name.endswith(".tar.gz") or name.endswith(".tgz"):
        safe_members = _safe_tar_members(archive_path)
        with tarfile.open(archive_path, "r:gz") as archive:
            try:
                archive.extractall(target_dir, members=safe_members, filter="data")
            except TypeError:
                archive.extractall(target_dir, members=safe_members)
        return
    raise ValueError(f"Unsupported cheat release archive: {archive_path.name}")


def _payload_root(staging_dir: Path) -> Path:
    current = staging_dir
    payload_markers = {"js", "cheat", "cheat-settings", "package.json"}
    while True:
        names = {path.name for path in current.iterdir()}
        if names & payload_markers:
            return current
        children = [path for path in current.iterdir() if path.is_dir()]
        files = [path for path in current.iterdir() if path.is_file()]
        if len(children) == 1 and not files:
            current = children[0]
            continue
        return current


def _destination_root(game_dir: Path, engine: str) -> Path:
    if engine == "mv":
        root = game_dir / "www"
        if not root.exists():
            raise ValueError(f"RPG Maker MV www folder not found: {root}")
    elif engine == "mz":
        root = game_dir
    else:
        raise ValueError(f"Unsupported cheat engine: {engine}")
    if not root.resolve().is_relative_to(game_dir.resolve()):
        raise ValueError(f"Destination root escapes game folder: {root}")
    return root


def _relative_to_game(game_dir: Path, path: Path) -> str:
    return path.relative_to(game_dir).as_posix()


def _safe_manifest_relative_path(value: str) -> Path:
    path = Path(value)
    if path.is_absolute() or ".." in path.parts:
        raise ValueError(f"Unsafe manifest relative path: {value}")
    return path


def _manifest_destination(game_dir: Path, relative_path: str) -> Path:
    path = _safe_manifest_relative_path(relative_path)
    destination = game_dir / path
    if not destination.resolve().is_relative_to(game_dir.resolve()):
        raise ValueError(f"Manifest destination escapes game folder: {relative_path}")
    return destination


def _manifest_backup_path(manifest: CheatManifest, record: CheatFileRecord) -> Path | None:
    if not record.backup_path:
        return None
    backup = Path(record.backup_path)
    backup_root = Path(manifest.backup_dir)
    if not backup.resolve().is_relative_to(backup_root.resolve()):
        raise ValueError(f"Manifest backup path escapes backup folder: {record.backup_path}")
    return backup


def _planned_files(payload_root: Path, destination_root: Path) -> list[tuple[Path, Path]]:
    planned: list[tuple[Path, Path]] = []
    root_resolved = destination_root.resolve()
    payload_resolved = payload_root.resolve()
    for source in payload_root.rglob("*"):
        if not source.is_file():
            continue
        if not source.resolve().is_relative_to(payload_resolved):
            raise ValueError(f"Unsafe source path: {source}")
        relative = source.relative_to(payload_root)
        destination = destination_root / relative
        if not destination.resolve().is_relative_to(root_resolved):
            raise ValueError(f"Unsafe destination path: {destination}")
        planned.append((source, destination))
    if not planned:
        raise ValueError("Cheat release zip did not contain installable files")
    return planned


def apply_cheat(game_dir: Path, engine: str, progress: Callable[[str], None] | None = None) -> CheatManifest:
    manifest_path = cheat_manifest_path(game_dir)
    if manifest_path.exists():
        raise ValueError(f"Cheat manifest already exists. Remove cheat first: {manifest_path}")
    if progress:
        progress("Downloading latest cheat plugin release from GitHub")
    archive_path, tag, asset_name = download_cheat_release(engine)
    destination_root = _destination_root(game_dir, engine)
    backup_dir = game_dir / "translator_work" / "cheat_backups" / f"cheat_backup_{_now_slug()}"
    records: list[CheatFileRecord] = []
    created_dirs: set[str] = set()
    with tempfile.TemporaryDirectory(prefix="game_translator_cheat_") as temp:
        staging = Path(temp)
        _extract_archive(archive_path, staging)
        payload = _payload_root(staging)
        planned = _planned_files(payload, destination_root)
        if progress:
            progress(f"Installing {len(planned)} cheat files")
        for source, destination in planned:
            destination.parent.mkdir(parents=True, exist_ok=True)
            for parent in [destination.parent, *destination.parent.parents]:
                if parent == game_dir.parent:
                    break
                if parent == game_dir:
                    break
                if game_dir in parent.parents:
                    created_dirs.add(_relative_to_game(game_dir, parent))
            source_hash = _sha256(source)
            backup_path: Path | None = None
            source_original_hash: str | None = None
            created = not destination.exists()
            overwrote = destination.exists()
            if overwrote:
                source_original_hash = _sha256(destination)
                backup_path = backup_dir / destination.relative_to(game_dir)
                backup_path.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(destination, backup_path)
            shutil.copy2(source, destination)
            records.append(CheatFileRecord(
                relative_path=_relative_to_game(game_dir, destination),
                backup_path=str(backup_path) if backup_path else None,
                source_sha256=source_original_hash,
                installed_sha256=source_hash,
                created=created,
                overwrote=overwrote,
            ))
    manifest = CheatManifest(
        schema_version=1,
        plugin=CHEAT_PLUGIN_NAME,
        plugin_source=CHEAT_REPO_URL,
        plugin_license="MIT",
        release_tag=tag,
        asset_name=asset_name,
        engine=engine,
        installed_at=_now_iso(),
        game_dir=str(game_dir),
        destination_root=_relative_to_game(game_dir, destination_root),
        backup_dir=str(backup_dir),
        files=records,
        created_dirs=sorted(created_dirs, key=lambda item: item.count("/"), reverse=True),
    )
    _write_manifest(manifest_path, manifest)
    if progress:
        progress(f"Cheat plugin installed. Toggle in game: Ctrl+C. Manifest -> {manifest_path}")
    return manifest


def remove_cheat(game_dir: Path, progress: Callable[[str], None] | None = None) -> CheatManifest:
    manifest_path = cheat_manifest_path(game_dir)
    if not manifest_path.exists():
        raise ValueError(f"No cheat install manifest found: {manifest_path}")
    manifest = _read_manifest(manifest_path)
    restored = 0
    deleted = 0
    skipped = 0
    for record in manifest.files:
        destination = _manifest_destination(game_dir, record.relative_path)
        if record.overwrote and record.backup_path:
            backup = _manifest_backup_path(manifest, record)
            if backup is None or not backup.exists():
                skipped += 1
                continue
            if not destination.exists() or _sha256(destination) != record.installed_sha256:
                skipped += 1
                continue
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(backup, destination)
            restored += 1
        elif record.created and destination.exists():
            if _sha256(destination) == record.installed_sha256:
                destination.unlink()
                deleted += 1
            else:
                skipped += 1
    for relative_dir in sorted(manifest.created_dirs, key=lambda item: item.count("/"), reverse=True):
        directory = _manifest_destination(game_dir, relative_dir)
        try:
            directory.rmdir()
        except OSError:
            pass
    removed_path = manifest_path.with_name(f"cheat_manifest_removed_{_now_slug()}.json")
    manifest_path.replace(removed_path)
    if progress:
        progress(f"Cheat removed: restored {restored}, deleted {deleted}, skipped {skipped}. Old manifest -> {removed_path}")
    return manifest
