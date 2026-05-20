from __future__ import annotations

import re
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from .app_logging import log_event
from .csv_store import load_results, save_results
from .glossary import format_glossary_for_prompt, load_glossary
from .llm import LLMProvider, make_provider
from .models import TextEntry, TranslationResult, text_identity
from .translation_memory import global_memory_path, load_memory, lookup_memory_value, save_memory
from .validate import translation_warnings

LogFn = Callable[[str], None]
ProgressFn = Callable[[int, int], None]


@dataclass(slots=True)
class TranslateOptions:
    target_lang: str
    source_lang: str | None = None
    provider: str = "google"
    model: str = "google"
    api_key: str | None = None
    api_base: str | None = None
    batch_size: int = 30
    workers: int = 1
    use_memory: bool = True
    save_memory: bool = True
    memory_paths: list[Path] | None = None
    glossary_path: Path | None = None
    dedupe_by_source: bool = True
    restart: bool = False
    on_log: LogFn | None = None
    stop_event: threading.Event | None = None


@dataclass(slots=True)
class TranslateReport:
    total_entries: int = 0
    translated: int = 0
    fallback: int = 0
    reused_memory: int = 0
    deduped_api_calls: int = 0
    invalid_llm_items: int = 0
    batches_retried: int = 0
    batches_failed: int = 0
    placeholder_warnings: int = 0


def _log(options: TranslateOptions, message: str) -> None:
    log_event(message)
    if options.on_log:
        options.on_log(message)


def _stopped(options: TranslateOptions) -> bool:
    return options.stop_event is not None and options.stop_event.is_set()


def dedupe_results(results: list[TranslationResult], wanted_ids: set[tuple[str, str]] | None = None) -> list[TranslationResult]:
    by_id: dict[tuple[str, str], TranslationResult] = {}
    for result in results:
        identity = text_identity(result.file, result.key)
        if wanted_ids is not None and identity not in wanted_ids:
            continue
        by_id[identity] = result
    return list(by_id.values())


def estimate_batch_size(entries: list[TextEntry], target_tokens: int = 8000) -> int:
    if not entries:
        return 30
    sample = entries[: min(20, len(entries))]
    avg_chars = sum(len(e.source) + len(e.context_text) for e in sample) / len(sample)
    avg_tokens = max(1, avg_chars / 3.5)
    return min(max(1, int(target_tokens / avg_tokens)), 60)


def dedupe_group_key(entry: TextEntry) -> tuple[str, str]:
    return (entry.source, entry.context_text)


def build_source_groups(entries: list[TextEntry]) -> dict[tuple[str, str], list[TextEntry]]:
    groups: dict[tuple[str, str], list[TextEntry]] = {}
    for entry in entries:
        groups.setdefault(dedupe_group_key(entry), []).append(entry)
    return groups


def fanout_results(
    batch_results: list[TranslationResult],
    groups: dict[tuple[str, str], list[TextEntry]],
    rep_identity_to_group: dict[tuple[str, str], tuple[str, str]],
) -> list[TranslationResult]:
    expanded: list[TranslationResult] = []
    for result in batch_results:
        gkey = rep_identity_to_group.get(text_identity(result.file, result.key))
        siblings = groups.get(gkey, []) if gkey else []
        if siblings:
            for entry in siblings:
                expanded.append(TranslationResult(entry.file, entry.key, entry.source, result.target, entry.context))
        else:
            expanded.append(result)
    return expanded


def is_retryable(exc: Exception) -> tuple[bool, float]:
    msg = str(exc)
    if "retry_after" in msg:
        m = re.search(r"['\"]retry_after['\"]\s*:\s*(\d+(?:\.\d+)?)", msg)
        if m:
            return True, float(m.group(1))
    if "524" in msg:
        return True, 60.0
    if "429" in msg:
        return True, 10.0
    if any(c in msg for c in ("503", "502", "500")):
        return True, 5.0
    if any(k in msg.lower() for k in ("timeout", "timed out", "connection", "truncated")):
        return True, 15.0
    return False, 0.0


def translate_batch_with_retry(
    provider: LLMProvider,
    batch: list[TextEntry],
    target_lang: str,
    source_lang: str | None,
    options: TranslateOptions,
    report: TranslateReport,
) -> list[TranslationResult]:
    for attempt in range(4):
        if _stopped(options):
            raise RuntimeError("Stopped by user")
        try:
            return provider.translate_batch(batch, target_lang, source_lang)
        except RuntimeError:
            raise
        except Exception as exc:
            retryable, suggested = is_retryable(exc)
            if retryable and attempt < 3:
                report.batches_retried += 1
                delay = max(suggested, 5.0 * (attempt + 1))
                _log(options, f"Batch error (retry {attempt + 1}/3 in {delay:.0f}s): {exc}")
                end = time.monotonic() + delay
                while time.monotonic() < end:
                    if _stopped(options):
                        raise RuntimeError("Stopped by user")
                    time.sleep(0.5)
            else:
                raise
    return []


def _make_provider(options: TranslateOptions) -> LLMProvider:
    provider = make_provider(options.provider, options.model, options.api_key, options.api_base)
    if options.glossary_path:
        entries = load_glossary(options.glossary_path)
        if entries:
            provider.set_glossary(format_glossary_for_prompt(entries))
            _log(options, f"Glossary: {len(entries)} entries from {options.glossary_path}")
    return provider


def run_translate(
    entries: list[TextEntry],
    translations_csv: Path,
    options: TranslateOptions,
    on_progress: ProgressFn | None = None,
) -> tuple[list[TranslationResult], TranslateReport]:
    report = TranslateReport(total_entries=len(entries))
    wanted_ids = {text_identity(entry.file, entry.key) for entry in entries}
    provider = _make_provider(options)

    existing: list[TranslationResult] = [] if options.restart else (load_results(translations_csv) if translations_csv.exists() else [])
    results = dedupe_results(existing, wanted_ids)
    completed = {text_identity(r.file, r.key) for r in results if r.target.strip() and r.target != r.source}

    memory_map = load_memory(
        (options.memory_paths or []) + [global_memory_path(), translations_csv.parent / "translation_memory.csv"],
        options.target_lang,
        options.source_lang,
    ) if options.use_memory else {}

    if memory_map:
        _log(options, f"Loaded {len(memory_map)} memory entries")

    pending = [e for e in entries if text_identity(e.file, e.key) not in completed]
    to_translate: list[TextEntry] = []
    for entry in pending:
        cached = lookup_memory_value(memory_map, entry.source, entry.context, options.target_lang, options.source_lang) if options.use_memory else None
        if cached:
            results.append(TranslationResult(entry.file, entry.key, entry.source, cached, entry.context))
            report.reused_memory += 1
        else:
            to_translate.append(entry)

    if report.reused_memory:
        results = dedupe_results(results, wanted_ids)
        save_results(results, translations_csv)
        _log(options, f"Reused {report.reused_memory} translations from memory")

    groups = build_source_groups(to_translate) if options.dedupe_by_source else {}
    rep_identity_to_group: dict[tuple[str, str], tuple[str, str]] = {}
    if options.dedupe_by_source:
        for gkey, group in groups.items():
            rep = group[0]
            rep_identity_to_group[text_identity(rep.file, rep.key)] = gkey
    unique_entries = [group[0] for group in groups.values()] if options.dedupe_by_source else list(to_translate)
    if options.dedupe_by_source and len(to_translate) > len(unique_entries):
        report.deduped_api_calls = len(to_translate) - len(unique_entries)
        _log(options, f"Pre-dedup: {len(to_translate)} -> {len(unique_entries)} unique (source + context)")

    raw_size = options.batch_size
    size = estimate_batch_size(unique_entries) if raw_size <= 0 else raw_size
    if raw_size <= 0:
        _log(options, f"Auto batch size: {size}")

    batches = [unique_entries[s : s + size] for s in range(0, len(unique_entries), size)]
    workers = max(1, min(8, options.workers))
    results_lock = threading.Lock()
    translated_count = len(results)
    failed_batches: list[list[TextEntry]] = []

    def process_batch(batch: list[TextEntry]) -> None:
        nonlocal translated_count, results
        try:
            batch_results = translate_batch_with_retry(provider, batch, options.target_lang, options.source_lang, options, report)
        except RuntimeError:
            raise
        except Exception as exc:
            report.batches_failed += 1
            _log(options, f"Batch failed (deferred): {exc}")
            failed_batches.append(batch)
            with results_lock:
                translated_count += sum(len(groups.get(rep_identity_to_group.get(text_identity(e.file, e.key), dedupe_group_key(e)), [e])) for e in batch)
                if on_progress:
                    on_progress(min(translated_count, len(entries)), len(entries))
            return

        expanded = fanout_results(batch_results, groups, rep_identity_to_group) if options.dedupe_by_source else batch_results
        for item in expanded:
            issues = translation_warnings(item.source, item.target)
            if issues:
                report.placeholder_warnings += 1
                if report.placeholder_warnings <= 5:
                    _log(options, f"Quality warning {item.file.name}:{item.key}: {', '.join(issues)}")
        with results_lock:
            translated_count += len(expanded)
            results.extend(expanded)
            results = dedupe_results(results, wanted_ids)
            save_results(results, translations_csv)
            if options.save_memory:
                sm = save_memory(translations_csv.parent / "translation_memory.csv", batch_results, options.target_lang, options.source_lang, options.provider)
                save_memory(global_memory_path(), batch_results, options.target_lang, options.source_lang, options.provider)
                for extra in options.memory_paths or []:
                    save_memory(extra, batch_results, options.target_lang, options.source_lang, options.provider)
                if sm:
                    _log(options, f"Saved {sm} translations to memory")
            _log(options, f"Translated {min(translated_count, len(entries))}/{len(entries)}")
            if on_progress:
                on_progress(min(translated_count, len(entries)), len(entries))

    if workers == 1:
        for batch in batches:
            if _stopped(options):
                raise RuntimeError("Stopped by user")
            process_batch(batch)
    else:
        import concurrent.futures

        executor = concurrent.futures.ThreadPoolExecutor(max_workers=workers)
        futures = {executor.submit(process_batch, batch): batch for batch in batches}
        try:
            for future in concurrent.futures.as_completed(futures):
                if _stopped(options):
                    for f in futures:
                        f.cancel()
                    raise RuntimeError("Stopped by user")
                future.result()
        finally:
            executor.shutdown(wait=False)

    if failed_batches and not _stopped(options):
        _log(options, f"--- Retrying {len(failed_batches)} deferred batch(es) ---")
        time.sleep(5)
        for batch in failed_batches:
            if _stopped(options):
                break
            subs = [batch] if len(batch) <= 10 else [batch[: len(batch) // 2], batch[len(batch) // 2 :]]
            for sub in subs:
                try:
                    batch_results = translate_batch_with_retry(provider, sub, options.target_lang, options.source_lang, options, report)
                    expanded = fanout_results(batch_results, groups, rep_identity_to_group) if options.dedupe_by_source else batch_results
                    with results_lock:
                        results.extend(expanded)
                        results = dedupe_results(results, wanted_ids)
                        save_results(results, translations_csv)
                    _log(options, f"Recovered {len(expanded)} entries")
                except Exception as exc:
                    _log(options, f"Deferred still failed (keeping source): {exc}")
                    with results_lock:
                        for ue in sub:
                            for entry in groups.get(rep_identity_to_group.get(text_identity(ue.file, ue.key), dedupe_group_key(ue)), [ue]):
                                results.append(TranslationResult(entry.file, entry.key, entry.source, entry.source, entry.context))
                        results = dedupe_results(results, wanted_ids)
                        save_results(results, translations_csv)

    report.translated = sum(1 for r in results if r.target.strip() and r.target != r.source)
    report.fallback = len(entries) - report.translated
    _log(options, f"--- Report: {report.translated}/{len(entries)} translated, {report.fallback} fallback, {report.reused_memory} memory ---")
    return results, report
