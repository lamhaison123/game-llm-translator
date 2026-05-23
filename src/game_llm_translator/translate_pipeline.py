from __future__ import annotations

import re
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from .app_logging import log_event
from .csv_store import load_results, save_results
from .glossary import apply_correction_table, build_auto_glossary, format_glossary_categorized, format_glossary_for_prompt, load_correction_table, load_glossary, load_glossary_with_categories
from .llm import LLMProvider, make_provider, postprocess_translation
from .models import TextEntry, TranslationResult, text_identity
from .translation_memory import global_memory_path, load_memory, lookup_memory_value, save_memory
from .validate import check_noun_consistency, format_noun_warnings, translation_warnings

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
    correction_table_path: Path | None = None
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
    if options.on_log:
        options.on_log(message)
    else:
        log_event(message)


STOPPED = "Stopped by user"


def _stopped(options: TranslateOptions) -> bool:
    return options.stop_event is not None and options.stop_event.is_set()


def _raise_if_stopped(options: TranslateOptions) -> None:
    if _stopped(options):
        raise RuntimeError(STOPPED)


def _is_stopped_error(exc: BaseException) -> bool:
    return isinstance(exc, RuntimeError) and str(exc) == STOPPED


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


def build_char_batches(
    entries: list[TextEntry],
    max_entries: int,
    max_chars: int = 12000,
) -> list[list[TextEntry]]:
    """Split entries into batches respecting both entry count and total char limits.

    Inspired by Translator++ maxRequestLength option. Prevents token overflow
    when individual entries are long (e.g. multi-paragraph descriptions).
    """
    batches: list[list[TextEntry]] = []
    current: list[TextEntry] = []
    current_chars = 0
    for entry in entries:
        entry_chars = len(entry.source) + len(entry.context_text)
        if current and (len(current) >= max_entries or current_chars + entry_chars > max_chars):
            batches.append(current)
            current = []
            current_chars = 0
        current.append(entry)
        current_chars += entry_chars
    if current:
        batches.append(current)
    return batches


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
                expanded.append(TranslationResult(entry.file, entry.key, entry.source, result.target, entry.context, sub_keys=entry.sub_keys if entry.sub_keys else result.sub_keys))
        else:
            expanded.append(result)
    return expanded


_REFUSAL_PATTERNS = (
    "我无法", "我不能", "无法给", "I cannot", "I'm unable", "I am unable",
    "I can't", "I apologize", "content policy", "violates", "against my",
    "as an ai", "as a language model",
    "thinking about your request", "processing your request",
    "let me think", "let me process",
)


def _is_content_refusal(exc: Exception) -> bool:
    """Return True if the LLM refused to translate (content filter / safety response)."""
    msg = str(exc).lower()
    return any(p.lower() in msg for p in _REFUSAL_PATTERNS)


def is_retryable(exc: Exception) -> tuple[bool, float]:
    if _is_content_refusal(exc):
        return False, 0.0
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
    if "not valid json" in msg.lower() or "expecting value" in msg.lower():
        return True, 5.0
    if "returned no choices" in msg.lower() or "returned empty" in msg.lower() or "returned no message" in msg.lower():
        return True, 5.0
    if "overloaded" in msg.lower():
        return True, 30.0
    return False, 0.0


def translate_batch_with_retry(
    provider: LLMProvider,
    batch: list[TextEntry],
    target_lang: str,
    source_lang: str | None,
    options: TranslateOptions,
    report: TranslateReport,
    report_lock: threading.Lock | None = None,
    _depth: int = 0,
) -> list[TranslationResult]:
    for attempt in range(4):
        _raise_if_stopped(options)
        try:
            return provider.translate_batch(batch, target_lang, source_lang)
        except Exception as exc:
            if _is_stopped_error(exc):
                raise
            retryable, suggested = is_retryable(exc)
            if retryable and attempt < 3:
                if report_lock is None:
                    report.batches_retried += 1
                else:
                    with report_lock:
                        report.batches_retried += 1
                delay = max(suggested, 5.0 * 2 ** attempt)
                _log(options, f"Batch error (retry {attempt + 1}/3 in {delay:.0f}s): {exc}")
                end = time.monotonic() + delay
                while time.monotonic() < end:
                    _raise_if_stopped(options)
                    time.sleep(0.5)
                if "524" in str(exc) and len(batch) > 1 and _depth < 6:
                    mid = len(batch) // 2
                    _log(options, f"524 timeout: splitting batch {len(batch)} -> {mid}+{len(batch)-mid} to reduce server load")
                    left = translate_batch_with_retry(provider, batch[:mid], target_lang, source_lang, options, report, report_lock, _depth + 1)
                    right = translate_batch_with_retry(provider, batch[mid:], target_lang, source_lang, options, report, report_lock, _depth + 1)
                    return left + right
            else:
                raise
    return []


def _make_provider(options: TranslateOptions, glossary_block: str = "") -> LLMProvider:
    """Create a provider, optionally injecting a pre-built glossary block.

    When *glossary_block* is non-empty it is used directly (avoids re-reading
    the CSV on each worker thread).  When it is empty and a glossary_path is
    set the file is loaded and formatted here.
    """
    provider = make_provider(options.provider, options.model, options.api_key, options.api_base)
    provider.stop_event = options.stop_event
    if glossary_block:
        provider.set_glossary(glossary_block)
    elif options.glossary_path:
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
    _raise_if_stopped(options)
    workers = max(1, min(8, options.workers))
    glossary_block = ""
    if options.glossary_path:
        cat_entries = load_glossary_with_categories(options.glossary_path)
        if cat_entries:
            has_categories = any(cat for _, _, cat in cat_entries)
            if has_categories:
                glossary_block = format_glossary_categorized(cat_entries)
                cat_names = sorted(set(cat for _, _, cat in cat_entries if cat))
                _log(options, f"Glossary: {len(cat_entries)} entries from {options.glossary_path} (categories: {', '.join(cat_names)})")
            else:
                flat_entries = [(t, tr) for t, tr, _ in cat_entries]
                glossary_block = format_glossary_for_prompt(flat_entries)
                _log(options, f"Glossary: {len(flat_entries)} entries from {options.glossary_path}")
    provider = _make_provider(options, glossary_block) if workers == 1 else None
    corrections = load_correction_table(options.correction_table_path)
    if corrections:
        _log(options, f"Correction table: {len(corrections)} rules from {options.correction_table_path}")

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

    batches = build_char_batches(unique_entries, max_entries=size)
    results_lock = threading.Lock()
    report_lock = threading.Lock()
    translated_count = len(results)
    failed_batches: list[list[TextEntry]] = []

    def _fanout_entry(e: TextEntry) -> list[TextEntry]:
        if options.dedupe_by_source:
            return groups.get(rep_identity_to_group.get(text_identity(e.file, e.key), dedupe_group_key(e)), [e])
        return [e]

    def process_batch(batch: list[TextEntry]) -> None:
        nonlocal translated_count, glossary_block
        if _stopped(options):
            return
        batch_glossary = glossary_block
        if provider is None and not batch_glossary:
            with results_lock:
                if results:
                    auto_entries = build_auto_glossary([(r.source, r.target, r.context) for r in results])
                    if auto_entries:
                        batch_glossary = format_glossary_for_prompt(auto_entries, max_chars=2000)
        batch_provider = provider if provider is not None else _make_provider(options, batch_glossary)
        try:
            batch_results = translate_batch_with_retry(batch_provider, batch, options.target_lang, options.source_lang, options, report, report_lock)
        except Exception as exc:
            if _stopped(options):
                raise RuntimeError(STOPPED) from exc
            with report_lock:
                report.batches_failed += 1
            if _is_content_refusal(exc):
                _log(options, f"WARN: LLM refused batch (content filter, fallback to source): {str(exc)[:120]}")
                with results_lock:
                    for e in batch:
                        for entry in _fanout_entry(e):
                            results.append(TranslationResult(entry.file, entry.key, entry.source, entry.source, entry.context))
                    deduped = dedupe_results(results, wanted_ids)
                    results.clear()
                    results.extend(deduped)
                    save_results(results, translations_csv)
            else:
                _log(options, f"Batch failed (deferred): {exc}")
                with results_lock:
                    failed_batches.append(batch)
            with results_lock:
                translated_count += sum(len(_fanout_entry(e)) for e in batch)
                if on_progress:
                    on_progress(min(translated_count, len(entries)), len(entries))
            return

        if _stopped(options):
            return

        missed = sum(1 for r in batch_results if r.target == r.source)
        if missed:
            _log(options, f"WARN: LLM missed {missed}/{len(batch_results)} entries (fallback to source)")
        expanded = fanout_results(batch_results, groups, rep_identity_to_group) if options.dedupe_by_source else batch_results
        if corrections:
            expanded = [
                TranslationResult(r.file, r.key, r.source, postprocess_translation(r.source, apply_correction_table(r.target, corrections)), r.context, sub_keys=r.sub_keys)
                for r in expanded
            ]
        else:
            expanded = [
                TranslationResult(r.file, r.key, r.source, postprocess_translation(r.source, r.target), r.context, sub_keys=r.sub_keys)
                for r in expanded
            ]
        for item in expanded:
            issues = translation_warnings(item.source, item.target, item.context)
            if issues:
                with report_lock:
                    report.placeholder_warnings += 1
                _log(options, f"Quality warning {item.file.name}:{item.key}: {', '.join(issues)}")
        if _stopped(options):
            return
        with results_lock:
            translated_count += len(expanded)
            results.extend(expanded)
            deduped = dedupe_results(results, wanted_ids)
            results.clear()
            results.extend(deduped)
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

    try:
        if workers == 1:
            for batch in batches:
                _raise_if_stopped(options)
                process_batch(batch)
                if provider is not None and len(batches) > 1:
                    with results_lock:
                        if results:
                            auto_entries = build_auto_glossary([(r.source, r.target, r.context) for r in results])
                            if auto_entries:
                                auto_block = format_glossary_for_prompt(auto_entries, max_chars=2000)
                                glossary_block = (glossary_block + "\n" + auto_block) if glossary_block else auto_block
                                provider.set_glossary(glossary_block)
        else:
            import concurrent.futures

            executor = concurrent.futures.ThreadPoolExecutor(max_workers=workers)
            futures: list[concurrent.futures.Future[None]] = []
            try:
                for batch in batches:
                    _raise_if_stopped(options)
                    futures.append(executor.submit(process_batch, batch))
                user_stopped = False
                for future in concurrent.futures.as_completed(futures):
                    if _stopped(options):
                        user_stopped = True
                        for pending in futures:
                            pending.cancel()
                        break
                    try:
                        future.result()
                    except RuntimeError as exc:
                        if _is_stopped_error(exc):
                            user_stopped = True
                            for pending in futures:
                                pending.cancel()
                            break
                        raise
                if user_stopped or _stopped(options):
                    _raise_if_stopped(options)
            finally:
                executor.shutdown(wait=False, cancel_futures=True)

        if failed_batches and not _stopped(options):
            _log(options, f"--- Retrying {len(failed_batches)} deferred batch(es) ---")
            end = time.monotonic() + 5
            while time.monotonic() < end:
                _raise_if_stopped(options)
                time.sleep(0.25)

            def retry_sub(sub: list[TextEntry]) -> None:
                """Recursively split and retry until batch size == 1 or success."""
                nonlocal translated_count
                if not sub or _stopped(options):
                    return
                try:
                    retry_provider = provider if provider is not None else _make_provider(options, glossary_block)
                    batch_results = translate_batch_with_retry(retry_provider, sub, options.target_lang, options.source_lang, options, report, report_lock)
                    if _stopped(options):
                        _raise_if_stopped(options)
                    expanded = fanout_results(batch_results, groups, rep_identity_to_group) if options.dedupe_by_source else batch_results
                    if corrections:
                        expanded = [
                            TranslationResult(r.file, r.key, r.source, apply_correction_table(r.target, corrections), r.context)
                            for r in expanded
                        ]
                    with results_lock:
                        results.extend(expanded)
                        deduped = dedupe_results(results, wanted_ids)
                        results.clear()
                        results.extend(deduped)
                        save_results(results, translations_csv)
                    _log(options, f"Recovered {len(expanded)} entries")
                except Exception as exc:
                    if _is_stopped_error(exc):
                        raise
                    if _is_content_refusal(exc):
                        _log(options, f"WARN: LLM refused sub-batch {len(sub)} entries (content filter, fallback to source)")
                        with results_lock:
                            for e in sub:
                                for entry in _fanout_entry(e):
                                    results.append(TranslationResult(entry.file, entry.key, entry.source, entry.source, entry.context))
                            deduped = dedupe_results(results, wanted_ids)
                            results.clear()
                            results.extend(deduped)
                            save_results(results, translations_csv)
                        return
                    if len(sub) > 1:
                        mid = len(sub) // 2
                        _log(options, f"Splitting failed batch ({len(sub)} -> {mid}+{len(sub)-mid}): {exc}")
                        retry_sub(sub[:mid])
                        if not _stopped(options):
                            retry_sub(sub[mid:])
                    else:
                        _log(options, f"Single entry still failed (keeping source): {exc}")
                        with results_lock:
                            entry = sub[0]
                            for e in _fanout_entry(entry):
                                results.append(TranslationResult(e.file, e.key, e.source, e.source, e.context))
                            deduped = dedupe_results(results, wanted_ids)
                            results.clear()
                            results.extend(deduped)
                            save_results(results, translations_csv)

            for batch in failed_batches:
                _raise_if_stopped(options)
                retry_sub(batch)
    except RuntimeError as exc:
        if _is_stopped_error(exc):
            _log(options, "Translation stopped by user.")
            raise
        raise

    if _stopped(options):
        _raise_if_stopped(options)

    noun_issues = check_noun_consistency(results)
    if noun_issues:
        for msg in format_noun_warnings(noun_issues):
            _log(options, f"CONSISTENCY: {msg}")

    report.translated = sum(1 for r in results if r.target.strip() and r.target != r.source)
    report.fallback = len(entries) - report.translated
    _log(options, f"--- Report: {report.translated}/{len(entries)} translated, {report.fallback} fallback, {report.reused_memory} memory ---")
    return results, report
