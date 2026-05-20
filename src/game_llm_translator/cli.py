from __future__ import annotations

from pathlib import Path
from typing import Literal

import typer
from rich.console import Console
from rich.progress import track

from .app_logging import log_event
from .auto import analyze_game, auto_translate_game, write_analysis_report
from .config import load_settings
from .csv_store import load_entries, load_results, save_entries, save_results
from .editor import open_file_editor
from .llm import make_provider
from .rpg_maker import apply_rpg_maker, extract_rpg_maker, extract_rpg_maker_mv, extract_rpg_maker_mz
from .xunity import apply_xunity, extract_xunity
from .unity import extract_unity
from .models import TranslationResult, text_identity
from .translation_memory import global_memory_path, load_memory, save_memory

app = typer.Typer(help="Translate RPG Maker and Unity game text via LLM API.")
console = Console()

GameType = Literal["rpg-maker", "rpg-maker-mv", "rpg-maker-mz", "unity", "unity-xunity"]


def _is_rpg_maker_type(game_type: GameType) -> bool:
    return game_type in {"rpg-maker", "rpg-maker-mv", "rpg-maker-mz"}


def _is_xunity_type(game_type: GameType) -> bool:
    return game_type == "unity-xunity"


def _extract(game_type: GameType, game_dir: Path):
    if game_type == "rpg-maker-mv":
        return extract_rpg_maker_mv(game_dir)
    if game_type == "rpg-maker-mz":
        return extract_rpg_maker_mz(game_dir)
    if _is_rpg_maker_type(game_type):
        return extract_rpg_maker(game_dir)
    if game_type == "unity-xunity":
        return extract_xunity(game_dir)
    if game_type == "unity":
        return extract_unity(game_dir)
    raise typer.BadParameter("game_type must be rpg-maker, rpg-maker-mv, rpg-maker-mz, unity-xunity, or unity")


@app.command()
def auto(
    game_dir: Path = typer.Argument(..., exists=True, file_okay=False),
    target_lang: str = typer.Option("Vietnamese", "--target", "-t"),
    source_lang: str | None = typer.Option(None, "--source", "-s"),
    provider: str = typer.Option("google", "--provider"),
    model: str | None = typer.Option(None, "--model"),
    batch_size: int = typer.Option(30, "--batch-size"),
    work_dir: Path | None = typer.Option(None, "--work-dir"),
    api_key: str | None = typer.Option(None, "--api-key", help="API key for OpenAI-compatible providers."),
    api_base: str | None = typer.Option(None, "--api-base", help="Base URL for OpenAI-compatible providers."),
    in_place: bool = typer.Option(False, "--in-place", help="Copy translated JSON into the game data folder after backup."),
    no_backup: bool = typer.Option(False, "--no-backup", help="Do not backup when using --in-place."),
    restart: bool = typer.Option(False, "--restart", help="Ignore existing translations.csv and translate from scratch."),
    no_memory: bool = typer.Option(False, "--no-memory", help="Disable translation memory reuse/save."),
    memory: Path | None = typer.Option(None, "--memory", help="Extra translation memory CSV."),
):
    """One-click RPG Maker translate flow: detect -> extract -> translate -> apply."""
    out = auto_translate_game(
        game_dir=game_dir,
        target_lang=target_lang,
        source_lang=source_lang,
        provider=provider,
        model=model or provider,
        api_key=api_key,
        api_base=api_base,
        batch_size=batch_size,
        work_dir=work_dir,
        backup=not no_backup,
        in_place=in_place,
        restart=restart,
        use_memory=not no_memory,
        memory_paths=[memory] if memory else None,
    )
    console.print(f"Auto translation complete -> {out}")


@app.command()
def scan(
    game_dir: Path = typer.Argument(..., exists=True, file_okay=False),
    provider: str = typer.Option("google", "--provider"),
    target_lang: str = typer.Option("Vietnamese", "--target", "-t"),
    out: Path | None = typer.Option(None, "--out", help="Write analysis JSON to this file."),
):
    """Analyze a game folder and report one-click translation readiness."""
    report = analyze_game(game_dir, provider, target_lang)
    console.print_json(data=report)
    if out:
        write_analysis_report(report, out)
        console.print(f"Wrote analysis report -> {out}")


@app.command()
def extract(
    game_type: GameType = typer.Argument(..., help="rpg-maker, rpg-maker-mv, rpg-maker-mz, unity-xunity, or unity"),
    game_dir: Path = typer.Argument(..., exists=True, file_okay=False),
    out: Path = typer.Option(Path("texts.csv"), "--out", "-o"),
):
    """Extract candidate text to CSV."""
    entries = _extract(game_type, game_dir)
    save_entries(entries, out)
    console.print(f"Extracted {len(entries)} entries -> {out}")


def _dedupe_results(results: list[TranslationResult], wanted_ids: set[tuple[str, str]]) -> list[TranslationResult]:
    by_id: dict[tuple[str, str], TranslationResult] = {}
    for result in results:
        identity = text_identity(result.file, result.key)
        if identity in wanted_ids:
            by_id[identity] = result
    return list(by_id.values())


def _translate_with_resume(entries, out: Path, target_lang: str, source_lang: str | None, provider: str | None, model: str | None, batch_size: int | None = None, api_key: str | None = None, api_base: str | None = None, use_memory: bool = True, memory: Path | None = None):
    settings = load_settings(provider, model, batch_size or 30)
    llm = make_provider(settings.provider, settings.model, api_key, api_base)
    existing = load_results(out) if out.exists() else []
    wanted_ids = {text_identity(entry.file, entry.key) for entry in entries}
    results = _dedupe_results(existing, wanted_ids)
    completed = {text_identity(result.file, result.key) for result in results if result.target.strip() and result.target != result.source}
    memory_map = {result.source: result.target for result in results if result.source.strip() and result.target.strip() and result.target != result.source}
    work_memory = out.parent / "translation_memory.csv"
    active_memory_paths = [global_memory_path(), work_memory]
    if memory:
        active_memory_paths.append(memory)
    if use_memory:
        persistent_memory = load_memory(active_memory_paths, target_lang, source_lang)
        memory_map.update(persistent_memory)
        if persistent_memory:
            console.print(f"Loaded {len(persistent_memory)} memory entries")
    pending = [entry for entry in entries if text_identity(entry.file, entry.key) not in completed]
    to_translate = []
    reused = 0
    for entry in pending:
        if entry.source in memory_map:
            results.append(TranslationResult(entry.file, entry.key, entry.source, memory_map[entry.source], entry.context))
            reused += 1
        else:
            to_translate.append(entry)
    if reused:
        console.print(f"Reused {reused} translations from memory")
        log_event(f"Reused {reused} translations from memory")
        results = _dedupe_results(results, wanted_ids)
        save_results(results, out)
    for start in track(range(0, len(to_translate), settings.batch_size), description="Translating"):
        batch = to_translate[start : start + settings.batch_size]
        batch_results = llm.translate_batch(batch, target_lang, source_lang)
        results.extend(batch_results)
        results = _dedupe_results(results, wanted_ids)
        for result in batch_results:
            if result.source.strip() and result.target.strip() and result.target != result.source:
                memory_map[result.source] = result.target
        save_results(results, out)
        if use_memory:
            saved_memory = save_memory(work_memory, batch_results, target_lang, source_lang, settings.provider)
            save_memory(global_memory_path(), batch_results, target_lang, source_lang, settings.provider)
            if memory:
                save_memory(memory, batch_results, target_lang, source_lang, settings.provider)
            if saved_memory:
                console.print(f"Saved {saved_memory} translations to memory")
                log_event(f"Saved {saved_memory} translations to memory")
    return results


@app.command()
def translate(
    input_csv: Path = typer.Argument(..., exists=True),
    out: Path = typer.Option(Path("translations.csv"), "--out", "-o"),
    target_lang: str = typer.Option("Vietnamese", "--target", "-t"),
    source_lang: str | None = typer.Option(None, "--source", "-s"),
    provider: str | None = typer.Option(None, "--provider"),
    model: str | None = typer.Option(None, "--model"),
    api_key: str | None = typer.Option(None, "--api-key", help="API key for OpenAI-compatible providers."),
    api_base: str | None = typer.Option(None, "--api-base", help="Base URL for OpenAI-compatible providers."),
    no_memory: bool = typer.Option(False, "--no-memory", help="Disable translation memory reuse/save."),
    memory: Path | None = typer.Option(None, "--memory", help="Extra translation memory CSV."),
    batch_size: int = typer.Option(30, "--batch-size"),
):
    """Translate CSV via configured LLM API."""
    entries = load_entries(input_csv)
    results = _translate_with_resume(entries, out, target_lang, source_lang, provider, model, batch_size, api_key, api_base, not no_memory, memory)
    console.print(f"Translated {len(results)} entries -> {out}")


@app.command()
def edit(
    csv_file: Path = typer.Argument(..., exists=True),
):
    """Open a translations CSV for manual editing."""
    open_file_editor(csv_file)
    console.print(f"Opened editor for {csv_file}")


@app.command()
def apply(
    game_type: GameType = typer.Argument(..., help="rpg-maker, rpg-maker-mv, rpg-maker-mz, unity-xunity, or unity"),
    translations_csv: Path = typer.Argument(..., exists=True),
    out_dir: Path = typer.Option(Path("translated_data"), "--out-dir"),
):
    """Apply translations. RPG Maker JSON and Unity XUnity TXT supported; legacy unity exports CSV."""
    results = load_results(translations_csv)
    if _is_rpg_maker_type(game_type):
        apply_rpg_maker(results, out_dir)
        console.print(f"Wrote RPG Maker translated JSON -> {out_dir}")
    elif _is_xunity_type(game_type):
        apply_xunity(results, out_dir)
        console.print(f"Wrote XUnity translated TXT -> {out_dir}")
    else:
        save_results(results, out_dir / "unity_translations_for_import.csv")
        console.print(f"Wrote Unity import CSV -> {out_dir / 'unity_translations_for_import.csv'}")


@app.command()
def pipeline(
    game_type: GameType = typer.Argument(...),
    game_dir: Path = typer.Argument(..., exists=True, file_okay=False),
    work_dir: Path = typer.Option(Path("work"), "--work-dir"),
    target_lang: str = typer.Option("Vietnamese", "--target", "-t"),
    provider: str | None = typer.Option(None, "--provider"),
    model: str | None = typer.Option(None, "--model"),
    api_key: str | None = typer.Option(None, "--api-key", help="API key for OpenAI-compatible providers."),
    api_base: str | None = typer.Option(None, "--api-base", help="Base URL for OpenAI-compatible providers."),
    no_memory: bool = typer.Option(False, "--no-memory", help="Disable translation memory reuse/save."),
    memory: Path | None = typer.Option(None, "--memory", help="Extra translation memory CSV."),
):
    """Extract -> translate -> apply/export."""
    work_dir.mkdir(parents=True, exist_ok=True)
    texts_csv = work_dir / "texts.csv"
    translations_csv = work_dir / "translations.csv"
    out_dir = work_dir / "translated"
    entries = _extract(game_type, game_dir)
    save_entries(entries, texts_csv)
    results = _translate_with_resume(entries, translations_csv, target_lang, None, provider, model, api_key=api_key, api_base=api_base, use_memory=not no_memory, memory=memory)
    if _is_rpg_maker_type(game_type):
        apply_rpg_maker(results, out_dir)
    elif _is_xunity_type(game_type):
        apply_xunity(results, out_dir)
    else:
        save_results(results, out_dir / "unity_translations_for_import.csv")
    console.print(f"Done -> {work_dir}")


if __name__ == "__main__":
    app()
