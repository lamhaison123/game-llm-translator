from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from collections import Counter
import json
import shutil

from .app_logging import log_event
from .csv_store import load_results, save_entries, save_results
from .models import TextEntry, TranslationResult, text_identity
from .translate_pipeline import TranslateOptions, dedupe_results, run_translate
from .rpg_maker import apply_rpg_maker, detect_rpg_maker, extract_rpg_maker, extract_rpg_maker_detailed, is_supported_json_engine
from .path_utils import timestamped_unique_path
from .xunity import apply_xunity, detect_xunity, extract_xunity
from .unity_setup import detect_unity_bare
from .font_replacement import ensure_game_fonts_support


def _data_dir(game_dir: Path) -> Path:
    return game_dir / "www" / "data" if (game_dir / "www" / "data").exists() else game_dir / "data"


def _vxace_data_dir(game_dir: Path) -> Path:
    return game_dir / "Data"


def _is_supported_engine(engine: str | None) -> bool:
    return is_supported_json_engine(engine) or engine == "vx-ace"


def analyze_game(game_dir: Path, provider: str = "google", target_lang: str = "Vietnamese") -> dict[str, object]:
    engine = detect_rpg_maker(game_dir)
    xunity_dir = detect_xunity(game_dir)
    if engine is None and xunity_dir is not None:
        engine = "unity-xunity"
    elif engine is None and detect_unity_bare(game_dir):
        engine = "unity-bare"
    if engine == "vx-ace":
        data_dir = _vxace_data_dir(game_dir)
        json_files: list[Path] = []
    else:
        data_dir = _data_dir(game_dir)
        json_files = sorted(data_dir.glob("*.json")) if data_dir.exists() else []
    extract_warnings: list[str] = []
    if engine == "unity-xunity":
        entries = extract_xunity(game_dir)
    elif _is_supported_engine(engine):
        entries, extract_warnings = extract_rpg_maker_detailed(game_dir)
    else:
        entries = []
    contexts = Counter(entry.context for entry in entries)
    files = Counter(entry.file.name for entry in entries)
    supported = (_is_supported_engine(engine) or engine == "unity-xunity") and engine not in {"xp", "vx"}
    return {
        "game_dir": str(game_dir),
        "engine": engine or "unknown",
        "data_dir": str(data_dir) if data_dir.exists() else (str(xunity_dir) if xunity_dir else ""),
        "json_files": len(json_files),
        "text_entries": len(entries),
        "contexts": dict(contexts),
        "top_files": dict(files.most_common(10)),
        "supported_auto_apply": supported,
        "unsupported_reason": (
            f"RPG Maker {engine.upper()} (RGSS) is not supported; use MV/MZ or VX Ace games."
            if engine in {"xp", "vx"}
            else (
                "Unity game detected but XUnity.AutoTranslator is not installed. "
                "Use 'Install BepInEx + XUnity' in the Apply tab, run the game once, then scan again."
                if engine == "unity-bare"
                else ""
            )
        ),
        "extract_warnings": extract_warnings,
        "recommended_command": f'game-translator auto "{game_dir}" --provider {provider} --target {target_lang}',
    }


def write_analysis_report(report: dict[str, object], file: Path) -> None:
    file.parent.mkdir(parents=True, exist_ok=True)
    file.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")


def _load_existing_results(translations_csv: Path, entries: list) -> list[TranslationResult]:
    if not translations_csv.exists():
        return []
    wanted_ids = {text_identity(entry.file, entry.key) for entry in entries}
    existing = load_results(translations_csv)
    return dedupe_results(existing, wanted_ids)


def auto_translate_game(
    game_dir: Path,
    target_lang: str = "Vietnamese",
    source_lang: str | None = None,
    provider: str = "google",
    model: str = "google",
    api_key: str | None = None,
    api_base: str | None = None,
    batch_size: int = 30,
    work_dir: Path | None = None,
    backup: bool = True,
    in_place: bool = False,
    restart: bool = False,
    use_memory: bool = True,
    save_memory: bool = True,
    memory_paths: list[Path] | None = None,
    glossary_path: Path | None = None,
    workers: int = 1,
    progress: Callable[[str], None] | None = None,
) -> Path:
    engine = detect_rpg_maker(game_dir)
    xunity_dir = detect_xunity(game_dir)
    if engine is None and xunity_dir is None:
        raise ValueError(f"Could not detect supported RPG Maker data or XUnity Translation folder in {game_dir}")
    is_xunity = engine is None and xunity_dir is not None
    if engine is None:
        engine = "unity-xunity"
    if engine in {"xp", "vx"}:
        raise ValueError(
            f"Detected RPG Maker {engine.upper()}; only MV/MZ and VX Ace are supported for auto-translate."
        )
    if not is_xunity and not _is_supported_engine(engine):
        raise ValueError(f"Detected {engine}, but automatic apply currently supports RPG Maker MV/MZ/VX Ace or Unity XUnity AutoTranslator games only")

    work_dir = work_dir or (game_dir / "translator_work")
    work_dir.mkdir(parents=True, exist_ok=True)
    texts_csv = work_dir / "texts.csv"
    translations_csv = work_dir / "translations.csv"
    out_dir = work_dir / "translated_data"

    extract_warnings: list[str] = []
    if is_xunity:
        entries = extract_xunity(game_dir)
    else:
        entries, extract_warnings = extract_rpg_maker_detailed(game_dir)
        for message in extract_warnings:
            log_event(message, level="WARN")
            if progress:
                progress(f"WARN: {message}")
    log_event(f"Extracted {len(entries)} text entries from {game_dir}")
    if progress:
        progress(f"Extracted {len(entries)} text entries")
    save_entries(entries, texts_csv)
    report = analyze_game(game_dir, provider, target_lang)
    report.update({"texts_csv": str(texts_csv), "translations_csv": str(translations_csv), "out_dir": str(out_dir)})
    write_analysis_report(report, work_dir / "analysis.json")

    existing_results: list[TranslationResult] = [] if restart else _load_existing_results(translations_csv, entries)
    resumed_entries = len(existing_results)
    initial_pending_entries = len(entries) - resumed_entries
    if progress:
        progress(f"Resume: {resumed_entries} done, {initial_pending_entries} pending")

    options = TranslateOptions(
        target_lang=target_lang,
        source_lang=source_lang,
        provider=provider,
        model=model,
        api_key=api_key,
        api_base=api_base,
        batch_size=batch_size,
        workers=max(1, min(8, workers)),
        use_memory=use_memory,
        save_memory=save_memory,
        memory_paths=memory_paths,
        glossary_path=glossary_path,
        restart=restart,
        on_log=progress,
    )
    results, translate_report = run_translate(
        entries,
        translations_csv,
        options,
        on_progress=None,
    )
    if progress and translate_report.reused_memory:
        progress(f"Reused {translate_report.reused_memory} translations from memory")
    if progress and translate_report.placeholder_warnings:
        progress(f"Placeholder warnings: {translate_report.placeholder_warnings}")

    if progress:
        progress("Applying translated files")
    if is_xunity:
        apply_xunity(results, out_dir)
        translated_glob = "*.txt"
    else:
        apply_rpg_maker(results, out_dir)
        translated_glob = "*.rvdata2" if engine == "vx-ace" else "*.json"
    manifest = {
        "game_dir": str(game_dir),
        "engine": engine,
        "provider": provider,
        "target_lang": target_lang,
        "texts_csv": str(texts_csv),
        "translations_csv": str(translations_csv),
        "out_dir": str(out_dir),
        "translated_files": sorted(str(file.relative_to(out_dir).as_posix()) for file in out_dir.rglob(translated_glob)),
        "translated_entries": len(results),
        "resumed_entries": resumed_entries,
        "initial_pending_entries": initial_pending_entries,
        "remaining_entries": max(len(entries) - len(results), 0),
        "restart": restart,
        "in_place": in_place,
        "extract_warnings": extract_warnings,
        "translated_count": translate_report.translated,
        "fallback_count": translate_report.fallback,
        "placeholder_warnings": translate_report.placeholder_warnings,
        "batches_retried": translate_report.batches_retried,
        "batches_failed": translate_report.batches_failed,
    }
    if in_place:
        if is_xunity:
            data_dir = xunity_dir if xunity_dir is not None else (game_dir / "Translation")
            target_subdir = data_dir / (target_lang.lower()[:2] if target_lang else "vi") / "Text"
            target_subdir.mkdir(parents=True, exist_ok=True)
            if backup:
                backup_dir = timestamped_unique_path(game_dir, "translation_backup_")
                shutil.copytree(data_dir, backup_dir)
                manifest["backup_dir"] = str(backup_dir)
                if progress:
                    progress(f"Backup created -> {backup_dir}")
            for file in out_dir.rglob("*.txt"):
                destination = target_subdir / file.name
                shutil.copy2(file, destination)
            manifest["applied_dir"] = str(target_subdir)
            if progress:
                progress(f"Applied translated files -> {target_subdir}")
            write_analysis_report(manifest, work_dir / "manifest.json")
            return target_subdir
        data_dir = _vxace_data_dir(game_dir) if engine == "vx-ace" else _data_dir(game_dir)
        if backup:
            backup_dir = timestamped_unique_path(game_dir, "data_backup_")
            shutil.copytree(data_dir, backup_dir)
            manifest["backup_dir"] = str(backup_dir)
            if progress:
                progress(f"Backup created -> {backup_dir}")
        copy_glob = "*.rvdata2" if engine == "vx-ace" else "*.json"
        for file in out_dir.rglob(copy_glob):
            destination = data_dir / file.relative_to(out_dir)
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(file, destination)
        # Auto-replace font for Vietnamese (MV/MZ only — VX Ace fonts handled at game level)
        if engine != "vx-ace":
            font_result = ensure_game_fonts_support(game_dir, target_lang)
            if font_result["replaced"]:
                log_event(f"Font replaced: {font_result.get('details', '')}", level="INFO")
                if progress:
                    progress(f"Font replaced: {font_result.get('details', '')}")
            manifest["font_replacement"] = font_result
        manifest["applied_dir"] = str(data_dir)
        if progress:
            progress(f"Applied translated files -> {data_dir}")
        write_analysis_report(manifest, work_dir / "manifest.json")
        return data_dir
    write_analysis_report(manifest, work_dir / "manifest.json")
    return out_dir
