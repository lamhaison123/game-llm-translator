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
from .rpg_maker import apply_rpg_maker, extract_rpg_maker, extract_rpg_maker_mv, extract_rpg_maker_mz
from .xunity import apply_xunity, extract_xunity
from .unity import extract_unity
from .models import TranslationResult, text_identity
from .translate_pipeline import TranslateOptions, run_translate

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
    glossary: Path | None = typer.Option(None, "--glossary", help="Glossary CSV (term, translation columns)."),
    workers: int = typer.Option(1, "--workers", min=1, max=8, help="Parallel translation workers."),
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
        glossary_path=glossary,
        workers=workers,
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
    if report.get("unsupported_reason"):
        console.print(f"[yellow]{report['unsupported_reason']}[/yellow]")
    for warning in report.get("extract_warnings", []):
        console.print(f"[yellow]WARN[/yellow] {warning}")
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
    if _is_rpg_maker_type(game_type):
        from .rpg_maker import extract_rpg_maker_detailed

        entries, warnings = extract_rpg_maker_detailed(game_dir)
        for warning in warnings:
            console.print(f"[yellow]WARN[/yellow] {warning}")
    else:
        entries = _extract(game_type, game_dir)
    save_entries(entries, out)
    console.print(f"Extracted {len(entries)} entries -> {out}")


def _translate_with_resume(
    entries,
    out: Path,
    target_lang: str,
    source_lang: str | None,
    provider: str | None,
    model: str | None,
    batch_size: int | None = None,
    api_key: str | None = None,
    api_base: str | None = None,
    use_memory: bool = True,
    memory: Path | None = None,
    glossary: Path | None = None,
    workers: int = 1,
):
    settings = load_settings(provider, model, batch_size or 30)
    memory_paths = [memory] if memory else None
    options = TranslateOptions(
        target_lang=target_lang,
        source_lang=source_lang,
        provider=settings.provider,
        model=settings.model,
        api_key=api_key,
        api_base=api_base,
        batch_size=batch_size or settings.batch_size,
        workers=workers,
        use_memory=use_memory,
        memory_paths=memory_paths,
        glossary_path=glossary,
        on_log=lambda msg: (console.print(msg), log_event(msg)),
    )
    results, report = run_translate(entries, out, options)
    console.print(
        f"Done: {report.translated}/{report.total_entries} translated, "
        f"{report.fallback} fallback, {report.reused_memory} from memory"
    )
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
    glossary: Path | None = typer.Option(None, "--glossary", help="Glossary CSV (term, translation columns)."),
    batch_size: int = typer.Option(30, "--batch-size"),
    workers: int = typer.Option(1, "--workers", min=1, max=8, help="Parallel translation workers."),
):
    """Translate CSV via configured LLM API."""
    entries = load_entries(input_csv)
    results = _translate_with_resume(
        entries, out, target_lang, source_lang, provider, model, batch_size, api_key, api_base,
        not no_memory, memory, glossary, workers,
    )
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
    glossary: Path | None = typer.Option(None, "--glossary", help="Glossary CSV."),
    workers: int = typer.Option(1, "--workers", min=1, max=8),
):
    """Extract -> translate -> apply/export."""
    work_dir.mkdir(parents=True, exist_ok=True)
    texts_csv = work_dir / "texts.csv"
    translations_csv = work_dir / "translations.csv"
    out_dir = work_dir / "translated"
    entries = _extract(game_type, game_dir)
    save_entries(entries, texts_csv)
    results = _translate_with_resume(
        entries, translations_csv, target_lang, None, provider, model,
        api_key=api_key, api_base=api_base, use_memory=not no_memory, memory=memory, glossary=glossary, workers=workers,
    )
    if _is_rpg_maker_type(game_type):
        apply_rpg_maker(results, out_dir)
    elif _is_xunity_type(game_type):
        apply_xunity(results, out_dir)
    else:
        save_results(results, out_dir / "unity_translations_for_import.csv")
    console.print(f"Done -> {work_dir}")


if __name__ == "__main__":
    app()
