from __future__ import annotations

from pathlib import Path
import sys
from typing import Any, Literal, cast

import typer
from rich.console import Console
from rich.progress import track

from .app_logging import log_event
from .auto import analyze_game, auto_translate_game, write_analysis_report
from .config import load_settings
from .csv_store import load_entries, load_results, save_entries, save_results
from .editor import open_file_editor
from .rpg_maker import apply_rpg_maker, extract_rpg_maker, extract_rpg_maker_mv, extract_rpg_maker_mz, extract_rpg_maker_vxace
from .xunity import apply_xunity, extract_xunity
from .unity import extract_unity
from .models import TranslationResult, TextEntry, text_identity
from .translate_pipeline import TranslateOptions, run_translate

app = typer.Typer(help="Translate RPG Maker and Unity game text via LLM API.")
if hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
console = Console(force_terminal=False, legacy_windows=False)

GameType = Literal["rpg-maker", "rpg-maker-mv", "rpg-maker-mz", "rpg-maker-vxace", "unity", "unity-xunity"]


def _is_rpg_maker_type(game_type: GameType) -> bool:
    return game_type in {"rpg-maker", "rpg-maker-mv", "rpg-maker-mz", "rpg-maker-vxace"}


def _is_xunity_type(game_type: GameType) -> bool:
    return game_type == "unity-xunity"


def _extract(game_type: GameType, game_dir: Path):
    if game_type == "rpg-maker-mv":
        return extract_rpg_maker_mv(game_dir)
    if game_type == "rpg-maker-mz":
        return extract_rpg_maker_mz(game_dir)
    if game_type == "rpg-maker-vxace":
        return extract_rpg_maker_vxace(game_dir)
    if _is_rpg_maker_type(game_type):
        return extract_rpg_maker(game_dir)
    if game_type == "unity-xunity":
        return extract_xunity(game_dir)
    if game_type == "unity":
        return extract_unity(game_dir)
    raise typer.BadParameter("game_type must be rpg-maker, rpg-maker-mv, rpg-maker-mz, rpg-maker-vxace, unity-xunity, or unity")


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
    no_save_memory: bool = typer.Option(False, "--no-save-memory", help="Disable saving new translations to memory (reuse is unaffected)."),
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
        save_memory=not (no_memory or no_save_memory),
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
    for warning in cast(list[Any], report.get("extract_warnings", [])):
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
    correction_table: Path | None = None,
    workers: int = 1,
):
    settings = load_settings(provider, model, batch_size or 30)
    memory_paths = [memory] if memory else None
    def on_log(msg: str) -> None:
        console.print(msg)
        log_event(msg)

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
        correction_table_path=correction_table,
        on_log=on_log,
    )

    # Pre-flight summary: show user what the pipeline will do BEFORE spending tokens.
    # Reads existing translations.csv (if any) to report resume state.
    if out.exists():
        try:
            existing = load_results(out)
            done = sum(1 for r in existing if r.target.strip() and r.target != r.source)
            fallback = sum(1 for r in existing if r.target.strip() and r.target == r.source)
            todo = len(entries) - done - fallback
            console.print(
                f"[cyan]Pre-flight:[/cyan] {done}/{len(entries)} done, "
                f"{fallback} fallback (target==source), {todo} to translate. "
                f"Provider: {settings.provider}/{settings.model}, workers={workers}."
            )
            if fallback:
                console.print(
                    f"[yellow]Tip:[/yellow] {fallback} fallback rows will be kept as-is. "
                    f"Use `game-translator retry {out} --filter fallback` to re-translate them."
                )
        except Exception:
            pass  # If the existing CSV is unreadable, skip the summary — run_translate will report.

    results, report = run_translate(entries, out, options)
    console.print(
        f"Done: {report.translated}/{report.total_entries} translated, "
        f"{report.fallback} fallback, {report.reused_memory} from memory"
    )
    return results


@app.command()
def retry(
    translations_csv: Path = typer.Argument(..., exists=True, help="Existing translations CSV with fallback rows or rows you want re-translated."),
    target_lang: str = typer.Option("Vietnamese", "--target", "-t"),
    source_lang: str | None = typer.Option(None, "--source", "-s"),
    provider: str | None = typer.Option(None, "--provider"),
    model: str | None = typer.Option(None, "--model"),
    api_key: str | None = typer.Option(None, "--api-key"),
    api_base: str | None = typer.Option(None, "--api-base"),
    no_memory: bool = typer.Option(False, "--no-memory"),
    memory: Path | None = typer.Option(None, "--memory"),
    glossary: Path | None = typer.Option(None, "--glossary"),
    correction_table: Path | None = typer.Option(None, "--correction-table"),
    batch_size: int = typer.Option(30, "--batch-size"),
    workers: int = typer.Option(1, "--workers", min=1, max=8),
    filter_kind: str = typer.Option("fallback", "--filter", help="Which rows to retry: 'fallback' (target==source), 'empty' (target==''), 'cjk-leak' (target contains CJK), 'needs-retry' (empty OR fallback OR cjk-leak), 'context=<value>', or 'all'."),
):
    """Re-translate selected rows from an existing translations CSV.

    Clears the target of rows matching --filter, then runs the normal translate
    pipeline. Other rows are preserved (resume logic skips them). Far cheaper
    than --restart when only a subset of rows need re-translation, e.g. after
    a 502 burst left a few dozen fallback rows.
    """
    from .validate import is_cjk_leak, needs_retry

    existing = load_results(translations_csv)
    if not existing:
        console.print(f"[red]{translations_csv}[/red] is empty or unreadable.")
        raise typer.Exit(code=1)

    def _matches(r: TranslationResult) -> bool:
        if filter_kind == "all":
            return True
        if filter_kind == "fallback":
            return r.target.strip() != "" and r.target == r.source
        if filter_kind == "empty":
            return r.target.strip() == ""
        if filter_kind == "cjk-leak":
            return r.target.strip() != "" and r.target != r.source and is_cjk_leak(r.target)
        if filter_kind == "needs-retry":
            return needs_retry(r.source, r.target)
        if filter_kind.startswith("context="):
            wanted = filter_kind.split("=", 1)[1]
            return r.context == wanted
        console.print(f"[red]Unknown --filter value: {filter_kind}[/red]")
        raise typer.Exit(code=1)

    cleared = 0
    rewritten: list[TranslationResult] = []
    for r in existing:
        if _matches(r):
            rewritten.append(TranslationResult(r.file, r.key, r.source, "", r.context, sub_keys=r.sub_keys))
            cleared += 1
        else:
            rewritten.append(r)

    if cleared == 0:
        console.print(f"No rows matched filter '{filter_kind}'. Nothing to do.")
        return

    save_results(rewritten, translations_csv)
    console.print(f"Cleared {cleared} rows (filter: {filter_kind}). Re-running translate pipeline.")

    # Reuse the existing translate flow by feeding entries back through the pipeline.
    # The resume logic will skip rows whose target is still set.
    entries = [
        TextEntry(r.file, r.key, r.source, r.context, "")
        for r in rewritten
    ]
    results = _translate_with_resume(
        entries, translations_csv, target_lang, source_lang, provider, model, batch_size,
        api_key, api_base, not no_memory, memory, glossary, correction_table, workers,
    )
    console.print(f"Done. {len(results)} entries in {translations_csv}.")


@app.command()
def validate(
    translations_csv: Path = typer.Argument(..., exists=True, file_okay=True, dir_okay=False),
    output: Path | None = typer.Option(None, "--out", "-o", help="Optional JSON report path. If omitted, prints summary to stdout."),
    strict: bool = typer.Option(False, "--strict", help="Exit non-zero if any warnings or noun inconsistencies are found."),
    glossary: Path | None = typer.Option(None, "--glossary", help="Also lint a glossary CSV for duplicate/blank/case-conflict terms."),
):
    """Validate a translations CSV without re-running the LLM pipeline.

    Reports placeholder mismatches, possible UI overflow, untranslated CJK
    namebox names, character-name inconsistencies across files, and fallback
    percentage. Use before `apply` to catch issues that would ship with the
    game patch.
    """
    import json
    from collections import Counter
    from .validate import translation_warnings, check_noun_consistency
    from .glossary import validate_glossary

    glossary_warnings = validate_glossary(glossary) if glossary else []

    results = load_results(translations_csv)
    if not results:
        console.print(f"[red]{translations_csv}[/red] is empty.")
        raise typer.Exit(code=1)

    warning_counter: Counter[str] = Counter()
    rows_with_warnings: list[dict[str, str]] = []
    fallback_count = 0
    empty_count = 0
    for r in results:
        if not r.target.strip():
            empty_count += 1
            continue
        if r.target == r.source:
            fallback_count += 1
        issues = translation_warnings(r.source, r.target, r.context)
        for issue in issues:
            # Strip parenthetical detail to group counts ("placeholder %1 missing (...)")
            key = issue.split("(", 1)[0].strip()
            warning_counter[key] += 1
        if issues:
            rows_with_warnings.append({
                "file": str(r.file), "key": r.key, "context": r.context,
                "source": r.source[:80], "target": r.target[:80],
                "issues": "; ".join(issues),
            })

    inconsistencies = check_noun_consistency(results)
    total = len(results)
    fallback_pct = (fallback_count / total * 100) if total else 0.0

    report = {
        "translations_csv": str(translations_csv),
        "total_rows": total,
        "empty_target": empty_count,
        "fallback_target_equals_source": fallback_count,
        "fallback_percent": round(fallback_pct, 2),
        "warning_counts": dict(warning_counter),
        "noun_inconsistencies": [
            {"source": src, "translations": variants[:10]}
            for src, variants in inconsistencies[:30]
        ],
        "rows_with_warnings": rows_with_warnings[:200],
        "glossary": {
            "path": str(glossary) if glossary else None,
            "warnings": glossary_warnings,
        },
    }

    if output:
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        console.print(f"Report written to {output}")
    else:
        console.print(f"Total rows: {total}")
        console.print(f"Empty target: {empty_count}, Fallback (target==source): {fallback_count} ({fallback_pct:.1f}%)")
        console.print(f"Warning counts: {dict(warning_counter)}")
        console.print(f"Noun inconsistencies: {len(inconsistencies)}")
        for src, variants in inconsistencies[:5]:
            console.print(f"  '{src}' -> {variants[:5]}")
        if glossary:
            console.print(f"Glossary warnings: {len(glossary_warnings)}")
            for w in glossary_warnings[:10]:
                console.print(f"  {w}")

    if strict and (warning_counter or inconsistencies or fallback_count or glossary_warnings):
        raise typer.Exit(code=2)


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
    correction_table: Path | None = typer.Option(None, "--correction-table", help="Correction table CSV (find, replace columns). Applied after each batch."),
    batch_size: int = typer.Option(30, "--batch-size"),
    workers: int = typer.Option(1, "--workers", min=1, max=8, help="Parallel translation workers."),
):
    """Translate CSV via configured LLM API."""
    entries = load_entries(input_csv)
    results = _translate_with_resume(
        entries, out, target_lang, source_lang, provider, model, batch_size, api_key, api_base,
        not no_memory, memory, glossary, correction_table, workers,
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
    if not results:
        console.print("No translations to apply.")
        return
    if _is_rpg_maker_type(game_type):
        apply_rpg_maker(results, out_dir)
        if game_type == "rpg-maker-vxace":
            console.print(f"Wrote RPG Maker VX Ace .rvdata2 -> {out_dir}")
        else:
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


@app.command()
def diff(
    old_texts: Path = typer.Argument(..., exists=True, file_okay=True, dir_okay=False, help="Old extraction CSV (e.g. from game v1.0)."),
    new_texts: Path = typer.Argument(..., exists=True, file_okay=True, dir_okay=False, help="New extraction CSV (e.g. from game v1.1)."),
    output: Path = typer.Option(..., "--out", "-o", help="Output CSV with status column."),
    translations: Path | None = typer.Option(None, "--translations", help="Old translations CSV; unchanged entries carry their translation forward."),
):
    """Compare two extractions and produce a translation-ready CSV.

    Use after a game patch: re-extract the new version, then run diff against
    the old extraction + your existing translations. The output CSV has a
    `status` column (unchanged/changed/new/removed) and pre-filled targets for
    unchanged entries. Pass that CSV to `translate` to fill in the rest.
    """
    from .diff_tool import run_diff

    report = run_diff(old_texts, new_texts, translations, output)
    s = report.stats
    console.print(
        f"Diff: {s['unchanged']} unchanged, {s['changed']} changed (need re-translate), "
        f"{s['new']} new, {s['removed']} removed -> {output}"
    )


if __name__ == "__main__":
    app()
