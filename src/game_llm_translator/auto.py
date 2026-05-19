from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from collections import Counter
import json
import shutil
import time

from .app_logging import log_event
from .csv_store import load_results, save_entries, save_results
from .llm import make_provider
from .models import TextEntry, TranslationResult, text_identity
from .translation_memory import global_memory_path, load_memory, save_memory
from .rpg_maker import apply_rpg_maker, detect_rpg_maker, extract_rpg_maker, is_supported_json_engine


def _data_dir(game_dir: Path) -> Path:
    return game_dir / "www" / "data" if (game_dir / "www" / "data").exists() else game_dir / "data"


def analyze_game(game_dir: Path, provider: str = "google", target_lang: str = "Vietnamese") -> dict[str, object]:
    engine = detect_rpg_maker(game_dir)
    data_dir = _data_dir(game_dir)
    json_files = sorted(data_dir.glob("*.json")) if data_dir.exists() else []
    entries = extract_rpg_maker(game_dir) if is_supported_json_engine(engine) else []
    contexts = Counter(entry.context for entry in entries)
    files = Counter(entry.file.name for entry in entries)
    return {
        "game_dir": str(game_dir),
        "engine": engine or "unknown",
        "data_dir": str(data_dir) if data_dir.exists() else "",
        "json_files": len(json_files),
        "text_entries": len(entries),
        "contexts": dict(contexts),
        "top_files": dict(files.most_common(10)),
        "supported_auto_apply": is_supported_json_engine(engine),
        "recommended_command": f'game-translator auto "{game_dir}" --provider {provider} --target {target_lang}',
    }


def write_analysis_report(report: dict[str, object], file: Path) -> None:
    file.parent.mkdir(parents=True, exist_ok=True)
    file.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")


def _dedupe_results(results: list[TranslationResult], wanted_ids: set[tuple[str, str]] | None = None) -> list[TranslationResult]:
    by_id: dict[tuple[str, str], TranslationResult] = {}
    for result in results:
        identity = text_identity(result.file, result.key)
        if wanted_ids is not None and identity not in wanted_ids:
            continue
        by_id[identity] = result
    return list(by_id.values())


def _load_existing_results(translations_csv: Path, entries: list) -> list[TranslationResult]:
    if not translations_csv.exists():
        return []
    wanted_ids = {text_identity(entry.file, entry.key) for entry in entries}
    existing = load_results(translations_csv)
    return _dedupe_results(existing, wanted_ids)


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
    memory_paths: list[Path] | None = None,
    progress: Callable[[str], None] | None = None,
) -> Path:
    engine = detect_rpg_maker(game_dir)
    if engine is None:
        raise ValueError(f"Could not detect supported RPG Maker data in {game_dir}")
    if not is_supported_json_engine(engine):
        raise ValueError(f"Detected {engine}, but automatic apply currently supports RPG Maker MV/MZ JSON games only")

    work_dir = work_dir or (game_dir / "translator_work")
    work_dir.mkdir(parents=True, exist_ok=True)
    texts_csv = work_dir / "texts.csv"
    translations_csv = work_dir / "translations.csv"
    out_dir = work_dir / "translated_data"

    entries = extract_rpg_maker(game_dir)
    log_event(f"Extracted {len(entries)} text entries from {game_dir}")
    if progress:
        progress(f"Extracted {len(entries)} text entries")
    save_entries(entries, texts_csv)
    report = analyze_game(game_dir, provider, target_lang)
    report.update({"texts_csv": str(texts_csv), "translations_csv": str(translations_csv), "out_dir": str(out_dir)})
    write_analysis_report(report, work_dir / "analysis.json")

    translator = make_provider(provider, model, api_key, api_base)
    existing_results: list[TranslationResult] = [] if restart else _load_existing_results(translations_csv, entries)
    resumed_entries = len(existing_results)
    results: list[TranslationResult] = list(existing_results)
    completed = {text_identity(result.file, result.key) for result in results if result.target.strip() and result.target != result.source}
    memory = {
        result.source: result.target
        for result in results
        if result.source.strip() and result.target.strip() and result.target != result.source
    }
    per_game_memory = work_dir / "translation_memory.csv"
    active_memory_paths = (memory_paths or []) + [global_memory_path(), per_game_memory]
    if use_memory:
        persistent_memory = load_memory(active_memory_paths, target_lang, source_lang)
        memory.update(persistent_memory)
        log_event(f"Loaded {len(persistent_memory)} memory entries")
        if progress:
            progress(f"Loaded {len(persistent_memory)} memory entries")
    pending_entries = [entry for entry in entries if text_identity(entry.file, entry.key) not in completed]
    initial_pending_entries = len(pending_entries)
    reused = 0
    to_translate: list[TextEntry] = []
    for entry in pending_entries:
        if entry.source in memory:
            results.append(TranslationResult(entry.file, entry.key, entry.source, memory[entry.source], entry.context))
            completed.add(text_identity(entry.file, entry.key))
            reused += 1
        else:
            to_translate.append(entry)
    if progress:
        progress(f"Resume: {resumed_entries} done, {initial_pending_entries} pending")
        if reused:
            progress(f"Reused {reused} translations from memory")
    if reused:
        log_event(f"Reused {reused} translations from memory")
    if results:
        results = _dedupe_results(results, {text_identity(entry.file, entry.key) for entry in entries})
        save_results(results, translations_csv)
    for start in range(0, len(to_translate), batch_size):
        batch = to_translate[start:start + batch_size]
        batch_results = translator.translate_batch(batch, target_lang, source_lang)
        results.extend(batch_results)
        results = _dedupe_results(results, {text_identity(entry.file, entry.key) for entry in entries})
        for result in batch_results:
            if result.source.strip() and result.target.strip() and result.target != result.source:
                memory[result.source] = result.target
        save_results(results, translations_csv)
        if use_memory:
            saved_memory = save_memory(per_game_memory, batch_results, target_lang, source_lang, provider)
            save_memory(global_memory_path(), batch_results, target_lang, source_lang, provider)
            if progress and saved_memory:
                progress(f"Saved {saved_memory} translations to memory")
            if saved_memory:
                log_event(f"Saved {saved_memory} translations to memory")
        if progress:
            progress(f"Translated {min(len(results), len(entries))}/{len(entries)} entries")

    if progress:
        progress("Applying translated JSON files")
    apply_rpg_maker(results, out_dir)
    manifest = {
        "game_dir": str(game_dir),
        "engine": engine,
        "provider": provider,
        "target_lang": target_lang,
        "texts_csv": str(texts_csv),
        "translations_csv": str(translations_csv),
        "out_dir": str(out_dir),
        "translated_files": sorted(str(file.relative_to(out_dir).as_posix()) for file in out_dir.rglob("*.json")),
        "translated_entries": len(results),
        "resumed_entries": resumed_entries,
        "initial_pending_entries": initial_pending_entries,
        "remaining_entries": max(len(entries) - len(results), 0),
        "restart": restart,
        "in_place": in_place,
    }
    if in_place:
        data_dir = _data_dir(game_dir)
        if backup:
            backup_dir = game_dir / f"data_backup_{time.strftime('%Y%m%d_%H%M%S')}"
            shutil.copytree(data_dir, backup_dir)
            manifest["backup_dir"] = str(backup_dir)
            if progress:
                progress(f"Backup created -> {backup_dir}")
        for file in out_dir.rglob("*.json"):
            destination = data_dir / file.relative_to(out_dir)
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(file, destination)
        manifest["applied_dir"] = str(data_dir)
        if progress:
            progress(f"Applied translated files -> {data_dir}")
        write_analysis_report(manifest, work_dir / "manifest.json")
        return data_dir
    write_analysis_report(manifest, work_dir / "manifest.json")
    return out_dir
