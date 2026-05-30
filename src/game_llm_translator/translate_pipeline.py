from __future__ import annotations

import re
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from .app_logging import log_event
from .csv_store import load_results, save_results
from .errors import STOPPED, StoppedByUser
from .glossary import apply_correction_table, build_auto_glossary, format_glossary_categorized, format_glossary_for_prompt, load_correction_table, load_glossary, load_glossary_with_categories, speaker_name_glossary_from_results
from .llm import LLMProvider, _NAMEBOX_PREFIX_RE, _replace_untranslated_namebox_names, extract_namebox_names, make_provider, postprocess_translation, translate_namebox_names
from .models import TextEntry, TranslationResult, text_identity
from .translation_memory import global_memory_path, load_memory, lookup_memory_value, save_memory
from .validate import check_noun_consistency, format_noun_warnings, repair_translation_syntax, translation_warnings

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
    on_batch_results: Callable[[list[TranslationResult]], None] | None = None
    name_translations: dict[str, str] | None = None


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


STOPPED = STOPPED  # re-exported for tests and external callers


def _stopped(options: TranslateOptions) -> bool:
    return options.stop_event is not None and options.stop_event.is_set()


def _raise_if_stopped(options: TranslateOptions) -> None:
    if _stopped(options):
        raise StoppedByUser()


def dedupe_results(results: list[TranslationResult], wanted_ids: set[tuple[str, str]] | None = None) -> list[TranslationResult]:
    by_id: dict[tuple[str, str], TranslationResult] = {}
    for result in results:
        identity = text_identity(result.file, result.key)
        if wanted_ids is not None and identity not in wanted_ids:
            continue
        by_id[identity] = result
    return list(by_id.values())


def _load_namebox_map(path: Path) -> dict[str, str]:
    """Load persisted speaker-name translations. Returns empty dict on any error
    so a corrupted file does not block a translation run."""
    import csv as _csv
    try:
        with path.open("r", newline="", encoding="utf-8-sig") as fp:
            return {row["original"]: row["translation"]
                    for row in _csv.DictReader(fp)
                    if row.get("original") and row.get("translation")}
    except (OSError, _csv.Error, KeyError, UnicodeDecodeError):
        return {}


def _save_namebox_map(path: Path, mapping: dict[str, str]) -> None:
    """Atomically write the namebox map; safe to call from any thread."""
    import csv as _csv
    from .csv_store import _atomic_write_text

    def _write(fp):
        writer = _csv.DictWriter(fp, fieldnames=["original", "translation"])
        writer.writeheader()
        for original, translation in sorted(mapping.items()):
            writer.writerow({"original": original, "translation": translation})

    _atomic_write_text(path, _write)


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


_DEDUP_CATEGORIES: dict[str, str] = {
    "rpg_maker_actors_name": "name", "rpg_maker_actors_nickname": "name",
    "rpg_maker_enemies_name": "name", "rpg_maker_skills_name": "name",
    "rpg_maker_items_name": "name", "rpg_maker_weapons_name": "name",
    "rpg_maker_armors_name": "name", "rpg_maker_states_name": "name",
    "rpg_maker_classes_name": "name", "rpg_maker_map_display_name": "name",
    "rpg_maker_system_gameTitle": "name", "rpg_maker_system_currencyUnit": "name",
    "rpg_maker_speaker_name": "name", "rpg_maker_troops_name": "name",
    "rpg_maker_map_event_name": "name", "rpg_maker_map_info_name": "name",
    "rpg_maker_common_event_name": "name", "rpg_maker_troop_name": "name",
    "rpg_maker_event_text": "dialogue", "rpg_maker_map_dialogue": "dialogue",
    "rpg_maker_map_comment": "dialogue",
    "rpg_maker_choice": "choice", "rpg_maker_map_choice": "choice",
    "rpg_maker_map_choice_label": "choice",
    "rpg_maker_vxace_note": "note",
    "rpg_maker_map_script_string": "script",
    "rpg_maker_vxace_script_string": "script",
    "rpg_maker_vxace_script_vocab_string": "script",
    "rpg_maker_map_actor_name": "script",
    "rpg_maker_skills_description": "description",
    "rpg_maker_items_description": "description",
    "rpg_maker_weapons_description": "description",
    "rpg_maker_armors_description": "description",
    "rpg_maker_states_description": "description",
    "rpg_maker_actors_profile": "description",
    "rpg_maker_terms_basic": "ui", "rpg_maker_terms_commands": "ui",
    "rpg_maker_terms_params": "ui", "rpg_maker_terms_messages": "ui",
    "rpg_maker_system_elements": "ui", "rpg_maker_system_weaponTypes": "ui",
    "rpg_maker_system_armorTypes": "ui", "rpg_maker_system_equipTypes": "ui",
    "rpg_maker_system_skillTypes": "ui",
    "rpg_maker_skills_message1": "ui", "rpg_maker_skills_message2": "ui",
    "rpg_maker_states_message1": "ui", "rpg_maker_states_message2": "ui",
    "rpg_maker_states_message3": "ui", "rpg_maker_states_message4": "ui",
}


def _dedup_category(context: str) -> str:
    return _DEDUP_CATEGORIES.get(context, "other")


def dedupe_group_key(entry: TextEntry) -> tuple[str, str]:
    return (entry.source, _dedup_category(entry.context))


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
                expanded.append(TranslationResult(entry.file, entry.key, entry.source, result.target, entry.context, sub_keys=entry.sub_keys))
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


_SPLIT_ERROR_MARKERS = ("524", "truncated", "max_tokens", "not valid json", "expecting value", "finish_reason=length")


def _should_split_on_error(exc: Exception) -> bool:
    """Errors where splitting the batch is more productive than retrying full.

    524 timeouts are server-side load; max_tokens/truncated are usually caused
    by one long entry — splitting isolates it. Same for JSON parse errors that
    repeat across retries (often a single problematic entry corrupting output).
    """
    msg = str(exc).lower()
    return any(marker.lower() in msg for marker in _SPLIT_ERROR_MARKERS)


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
        except StoppedByUser:
            raise
        except Exception as exc:
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
                if _should_split_on_error(exc) and len(batch) > 1 and _depth < 6:
                    mid = len(batch) // 2
                    _log(options, f"Splitting batch {len(batch)} -> {mid}+{len(batch)-mid} (cause: {str(exc)[:60]})")
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
    completed = {text_identity(r.file, r.key) for r in results if r.target.strip()}
    fallback_in_completed = sum(1 for r in results if r.target.strip() and r.target == r.source)
    if fallback_in_completed and not options.restart:
        _log(options, f"Note: {fallback_in_completed} fallback entries (target==source) will be kept as-is. Use --restart to re-translate them.")

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
    glossary_lock = threading.Lock()
    name_translations_lock = threading.Lock()
    # Coordination for concurrent namebox-name translations across workers.
    # in_flight tracks names a worker has claimed but not yet finished translating;
    # cond lets other workers wait until those translations land so their batch's
    # glossary stays consistent without duplicating LLM calls for the same name.
    name_translations_cond = threading.Condition(name_translations_lock)
    name_translations_in_flight: set[str] = set()
    translated_count = len(results)
    failed_batches: list[list[TextEntry]] = []
    # Shared across batches for consistent namebox name translations.
    # Persisted to disk so resume runs (and re-translate of unrelated batches)
    # don't re-spend tokens on names that were already translated previously.
    namebox_csv = translations_csv.parent / (translations_csv.stem + ".namebox.csv")
    name_translations_map = dict(options.name_translations or {})
    if not options.restart and namebox_csv.exists():
        loaded = _load_namebox_map(namebox_csv)
        if loaded:
            # Existing constructor entries win (e.g. an explicit override from caller).
            for k, v in loaded.items():
                name_translations_map.setdefault(k, v)
            _log(options, f"Loaded {len(loaded)} namebox names from {namebox_csv.name}")

    # Pre-pass: extract ALL unique CJK namebox names from the entire workload and
    # translate them in a single LLM call before the main loop. This cuts N extra
    # round-trips (one per batch with new names) down to 1, and ensures every
    # worker sees the full name map from the first batch onwards.
    all_namebox_names: dict[str, str] = {}
    for batch in batches:
        for name, content in extract_namebox_names(batch).items():
            if name not in all_namebox_names and name not in name_translations_map:
                all_namebox_names[name] = content
    if all_namebox_names:
        try:
            prepass_provider = provider if provider is not None else _make_provider(options, glossary_block)
            new_translations = translate_namebox_names(
                prepass_provider, all_namebox_names, options.target_lang, options.source_lang
            )
            if new_translations:
                name_translations_map.update(new_translations)
                names_str = ", ".join(f"{k}→{v}" for k, v in new_translations.items())
                _log(options, f"Pre-translated {len(new_translations)} namebox names: {names_str}")
                try:
                    _save_namebox_map(namebox_csv, name_translations_map)
                except Exception as exc:
                    _log(options, f"WARN: could not persist namebox map: {exc}")
        except StoppedByUser:
            raise
        except Exception as exc:
            _log(options, f"WARN: namebox pre-pass failed (will fall back to per-batch): {exc}")

    def _fanout_entry(e: TextEntry) -> list[TextEntry]:
        if options.dedupe_by_source:
            return groups.get(rep_identity_to_group.get(text_identity(e.file, e.key), dedupe_group_key(e)), [e])
        return [e]

    def _finalize_batch_results(batch_results: list[TranslationResult]) -> list[TranslationResult]:
        """Apply the standard post-LLM chain to a batch of results.

        Order matters: fanout (apply LLM result to all duplicates of the same
        source) → corrections → postprocess (restore masked tokens, normalize
        formatting) → replace any untranslated CJK namebox names from the shared
        map. Used by both the main batch path and the recursive retry path so
        both paths produce identically post-processed output.
        """
        expanded = (
            fanout_results(batch_results, groups, rep_identity_to_group)
            if options.dedupe_by_source else batch_results
        )
        if corrections:
            expanded = [
                TranslationResult(
                    r.file, r.key, r.source,
                    postprocess_translation(r.source, apply_correction_table(r.target, corrections)),
                    r.context, sub_keys=r.sub_keys,
                )
                for r in expanded
            ]
        else:
            expanded = [
                TranslationResult(
                    r.file, r.key, r.source,
                    postprocess_translation(r.source, r.target),
                    r.context, sub_keys=r.sub_keys,
                )
                for r in expanded
            ]
        with name_translations_lock:
            current_name_translations = dict(name_translations_map)
        if current_name_translations:
            expanded = [
                TranslationResult(
                    r.file, r.key, r.source,
                    _replace_untranslated_namebox_names(r.target, r.source, current_name_translations),
                    r.context, sub_keys=r.sub_keys,
                )
                for r in expanded
            ]
        return expanded

    def _record_source_fallback(entries_to_record: list[TextEntry]) -> None:
        """Append fallback (target = source, possibly with namebox name swap)
        TranslationResults for entries we could not translate, dedupe, save.

        Called from two paths: content-refusal on the main batch, and
        content-refusal / final single-entry failure during recursive retry.
        Caller must hold no locks; this acquires results_lock and
        name_translations_lock as needed.
        """
        with results_lock:
            for e in entries_to_record:
                for entry in _fanout_entry(e):
                    source_text = entry.source
                    with name_translations_lock:
                        if name_translations_map and _NAMEBOX_PREFIX_RE.match(entry.source):
                            source_text = _replace_untranslated_namebox_names(
                                entry.source, entry.source, name_translations_map,
                            )
                    results.append(TranslationResult(entry.file, entry.key, entry.source, source_text, entry.context))
            deduped = dedupe_results(results, wanted_ids)
            results.clear()
            results.extend(deduped)
            save_results(results, translations_csv)

    def process_batch(batch: list[TextEntry]) -> None:
        nonlocal translated_count
        if _stopped(options):
            return
        with glossary_lock:
            batch_glossary = glossary_block
        if provider is None and not batch_glossary:
            with results_lock:
                snapshot = list(results)
            if snapshot:
                snapshot_terms = [(r.source, r.target, r.context) for r in snapshot]
                auto_entries = build_auto_glossary(snapshot_terms)
                speaker_entries = speaker_name_glossary_from_results(snapshot_terms)
                if speaker_entries:
                    speaker_terms = {term for term, _ in speaker_entries}
                    auto_entries = speaker_entries + [entry for entry in auto_entries if entry[0] not in speaker_terms]
                if auto_entries:
                    batch_glossary = format_glossary_for_prompt(auto_entries, max_chars=2000)

        # Pre-translate CJK namebox speaker names
        namebox_names = extract_namebox_names(batch)
        if namebox_names:
            # Claim names this worker will translate; record names other workers are
            # already translating so we can wait for them. The claim happens under the
            # condition lock so two workers cannot independently claim the same name.
            names_we_translate: dict[str, str] = {}
            names_to_wait_for: set[str] = set()
            with name_translations_cond:
                for n, c in namebox_names.items():
                    if n in name_translations_map:
                        continue
                    if n in name_translations_in_flight:
                        names_to_wait_for.add(n)
                    else:
                        name_translations_in_flight.add(n)
                        names_we_translate[n] = c

            if names_we_translate:
                batch_provider_tmp = provider if provider is not None else _make_provider(options, batch_glossary)
                new_translations: dict[str, str] = {}
                try:
                    new_translations = translate_namebox_names(
                        batch_provider_tmp, names_we_translate, options.target_lang, options.source_lang
                    ) or {}
                    if new_translations:
                        names_str = ", ".join(f"{k}→{v}" for k, v in new_translations.items())
                        _log(options, f"Namebox names: {names_str}")
                except StoppedByUser:
                    raise
                except Exception as exc:
                    _log(options, f"WARN: namebox name translation failed: {exc}")
                finally:
                    # Release in-flight regardless of success; publish any translations we got
                    # and wake any workers waiting on these names. Runs on `raise` too.
                    with name_translations_cond:
                        name_translations_map.update(new_translations)
                        name_translations_in_flight.difference_update(names_we_translate.keys())
                        name_translations_cond.notify_all()
                        map_snapshot = dict(name_translations_map) if new_translations else None
                    # Persist outside the lock so other workers don't block on disk I/O.
                    # Only writes when we actually produced new translations.
                    if map_snapshot is not None:
                        try:
                            _save_namebox_map(namebox_csv, map_snapshot)
                        except Exception as exc:
                            _log(options, f"WARN: could not persist namebox map: {exc}")

            # Wait for translations claimed by other workers so this batch's glossary is complete.
            # Use a short timeout + stop-check so a pressed Stop button can unblock us.
            if names_to_wait_for:
                with name_translations_cond:
                    while any(n in name_translations_in_flight for n in names_to_wait_for):
                        if _stopped(options):
                            break
                        name_translations_cond.wait(timeout=0.5)

            # Snapshot the map for glossary construction (includes ours + others' translations).
            with name_translations_lock:
                batch_name_translations = dict(name_translations_map)
            # Inject name translations into glossary
            if batch_name_translations:
                # Include both current-batch nameboxes AND previously-translated names
                # from the shared map. Cross-batch consistency requires the LLM to see
                # established speaker translations whenever the name appears anywhere in
                # the dialogue — not only when it is the current batch's namebox prefix.
                name_gloss_lines = [
                    f'- "{n}" -> "{t}"\n'
                    for n, t in batch_name_translations.items()
                    if n in namebox_names or n in name_translations_map
                ]
                if name_gloss_lines:
                    name_glossary = "## Speaker Name Translations (apply inside <...> namebox brackets)\n" + "".join(name_gloss_lines)
                    batch_glossary = (batch_glossary + "\n" + name_glossary) if batch_glossary else name_glossary

        batch_provider = provider if provider is not None else _make_provider(options, batch_glossary)
        # Ensure single-worker provider gets the updated glossary with name translations
        if provider is not None:
            provider.set_glossary(batch_glossary)
        try:
            batch_results = translate_batch_with_retry(batch_provider, batch, options.target_lang, options.source_lang, options, report, report_lock)
        except StoppedByUser:
            raise
        except Exception as exc:
            if _stopped(options):
                raise StoppedByUser() from exc
            with report_lock:
                report.batches_failed += 1
            if _is_content_refusal(exc):
                _log(options, f"WARN: LLM refused batch (content filter, fallback to source): {str(exc)[:120]}")
                _record_source_fallback(batch)
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
        expanded = _finalize_batch_results(batch_results)
        expanded = [
            TranslationResult(
                item.file, item.key, item.source,
                repair_translation_syntax(item.source, item.target, item.context),
                item.context, sub_keys=item.sub_keys, extra=item.extra,
            )
            for item in expanded
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
        if options.on_batch_results:
            try:
                options.on_batch_results(list(expanded))
            except Exception:
                pass

    try:
        if workers == 1:
            for batch in batches:
                _raise_if_stopped(options)
                process_batch(batch)
                if provider is not None and len(batches) > 1:
                    with results_lock:
                        if results:
                            result_terms = [(r.source, r.target, r.context) for r in results]
                            auto_entries = build_auto_glossary(result_terms)
                            speaker_entries = speaker_name_glossary_from_results(result_terms)
                            if speaker_entries:
                                speaker_terms = {term for term, _ in speaker_entries}
                                auto_entries = speaker_entries + [entry for entry in auto_entries if entry[0] not in speaker_terms]
                            if auto_entries:
                                auto_block = format_glossary_for_prompt(auto_entries, max_chars=2000)
                                with glossary_lock:
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
                    except StoppedByUser:
                        user_stopped = True
                        for pending in futures:
                            pending.cancel()
                        break
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
                    expanded = _finalize_batch_results(batch_results)
                    with results_lock:
                        results.extend(expanded)
                        deduped = dedupe_results(results, wanted_ids)
                        results.clear()
                        results.extend(deduped)
                        save_results(results, translations_csv)
                    _log(options, f"Recovered {len(expanded)} entries")
                except StoppedByUser:
                    raise
                except Exception as exc:
                    if _is_content_refusal(exc):
                        _log(options, f"WARN: LLM refused sub-batch {len(sub)} entries (content filter, fallback to source)")
                        _record_source_fallback(sub)
                        return
                    if len(sub) > 1:
                        mid = len(sub) // 2
                        _log(options, f"Splitting failed batch ({len(sub)} -> {mid}+{len(sub)-mid}): {exc}")
                        retry_sub(sub[:mid])
                        if not _stopped(options):
                            retry_sub(sub[mid:])
                    else:
                        _log(options, f"Single entry still failed (keeping source): {exc}")
                        _record_source_fallback([sub[0]])

            for batch in failed_batches:
                _raise_if_stopped(options)
                retry_sub(batch)
    except StoppedByUser:
        _log(options, "Translation stopped by user.")
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
    if name_translations_map:
        try:
            _save_namebox_map(namebox_csv, name_translations_map)
        except Exception as exc:
            _log(options, f"WARN: could not persist namebox map: {exc}")
    return results, report
