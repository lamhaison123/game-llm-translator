from __future__ import annotations

import csv
import re as _re
from pathlib import Path

_NAMEBOX_RE = _re.compile(r"<([^<>]{1,80})>")


def load_glossary(path: Path | None) -> list[tuple[str, str]]:
    """Load (term, translation) pairs from a CSV with columns: term, translation, [note].

    Returns an empty list if path is None, the file doesn't exist, or it cannot be parsed.
    Rows missing term or translation are skipped silently.
    """
    if path is None or not path.exists():
        return []
    entries: list[tuple[str, str]] = []
    seen: set[str] = set()
    try:
        with path.open("r", newline="", encoding="utf-8-sig") as fp:
            reader = csv.DictReader(fp)
            for row in reader:
                term = (row.get("term") or "").strip()
                translation = (row.get("translation") or "").strip()
                if not term or not translation:
                    continue
                if term in seen:
                    continue
                seen.add(term)
                entries.append((term, translation))
    except (OSError, csv.Error, UnicodeDecodeError):
        return []
    return entries


def load_glossary_with_categories(path: Path | None) -> list[tuple[str, str, str]]:
    """Load (term, translation, category) triples from a CSV.

    Columns: term, translation, [note], [category].
    Category defaults to empty string if not present.
    """
    if path is None or not path.exists():
        return []
    entries: list[tuple[str, str, str]] = []
    seen: set[str] = set()
    try:
        with path.open("r", newline="", encoding="utf-8-sig") as fp:
            reader = csv.DictReader(fp)
            for row in reader:
                term = (row.get("term") or "").strip()
                translation = (row.get("translation") or "").strip()
                category = (row.get("category") or "").strip()
                if not term or not translation:
                    continue
                if term in seen:
                    continue
                seen.add(term)
                entries.append((term, translation, category))
    except (OSError, csv.Error, UnicodeDecodeError):
        return []
    return entries


def load_correction_table(path: Path | None) -> list[tuple[str, str]]:
    """Load (find, replace) pairs from a CSV with columns: find, replace.

    Applied as post-processing after translation to enforce consistent terminology.
    Rows missing 'find' are skipped. 'replace' can be empty (to delete a term).
    Returns an empty list if path is None or file doesn't exist.
    """
    if path is None or not path.exists():
        return []
    entries: list[tuple[str, str]] = []
    try:
        with path.open("r", newline="", encoding="utf-8-sig") as fp:
            reader = csv.DictReader(fp)
            for row in reader:
                find = (row.get("find") or "").strip()
                replace = (row.get("replace") or "").strip()
                if not find:
                    continue
                entries.append((find, replace))
    except (OSError, csv.Error, UnicodeDecodeError):
        return []
    return entries


def apply_correction_table(text: str, corrections: list[tuple[str, str]]) -> str:
    """Apply correction table entries as word-boundary-aware replacements.

    Uses word boundaries when the find term consists of ASCII alphanumerics,
    preventing partial matches (e.g. "HP" won't match inside "HPRecovery").
    For ASCII terms adjacent to CJK characters, also matches since CJK acts
    as a natural word boundary. Falls back to plain replacement for terms
    containing non-ASCII or non-alphanumeric characters.
    """
    for find, replace in corrections:
        if not find:
            continue
        if find.isascii() and find.isalnum():
            text = _re.sub(r'(?<![A-Za-z0-9_])' + _re.escape(find) + r'(?![A-Za-z0-9_])', replace, text)
        else:
            text = text.replace(find, replace)
    return text


def format_glossary_for_prompt(entries: list[tuple[str, str]], max_chars: int = 4000) -> str:
    """Render glossary entries as a system prompt addon block.

    Truncates gracefully when total exceeds max_chars; appends a note if truncated.
    Returns an empty string if entries is empty.
    """
    if not entries:
        return ""
    header = "## Glossary (use these EXACT translations for these terms; never substitute)\n"
    lines: list[str] = []
    used = len(header)
    truncated = 0
    for term, translation in entries:
        line = f'- "{term}" -> "{translation}"\n'
        if used + len(line) > max_chars:
            truncated = len(entries) - len(lines)
            break
        lines.append(line)
        used += len(line)
    block = header + "".join(lines)
    if truncated:
        block += f"(...{truncated} more terms omitted to keep prompt short)\n"
    return block


def format_glossary_categorized(entries: list[tuple[str, str, str]], max_chars: int = 4000) -> str:
    """Render glossary entries grouped by category for the system prompt.

    Entries with a non-empty category are grouped under sub-headers.
    Entries without a category are listed under the main header.
    Returns an empty string if entries is empty.
    """
    if not entries:
        return ""
    uncategorized: list[tuple[str, str]] = []
    categories: dict[str, list[tuple[str, str]]] = {}
    for term, translation, category in entries:
        if category:
            categories.setdefault(category, []).append((term, translation))
        else:
            uncategorized.append((term, translation))
    parts: list[str] = []
    total_used = 0
    header = "## Glossary (use these EXACT translations for these terms; never substitute)\n"
    parts.append(header)
    total_used += len(header)
    truncated = False
    for term, translation in uncategorized:
        line = f'- "{term}" -> "{translation}"\n'
        if total_used + len(line) > max_chars:
            truncated = True
            break
        parts.append(line)
        total_used += len(line)
    if not truncated:
        for cat_name, cat_entries in sorted(categories.items()):
            cat_header = f"### {cat_name}\n"
            if total_used + len(cat_header) > max_chars:
                truncated = True
                break
            parts.append(cat_header)
            total_used += len(cat_header)
            for term, translation in cat_entries:
                line = f'- "{term}" -> "{translation}"\n'
                if total_used + len(line) > max_chars:
                    truncated = True
                    break
                parts.append(line)
                total_used += len(line)
            if truncated:
                break
    if truncated:
        parts.append("(...more terms omitted to keep prompt short)\n")
    return "".join(parts)


def validate_glossary(path: Path | None) -> list[str]:
    """Inspect a glossary CSV and return human-readable warnings.

    Catches issues the loader silently drops (duplicate term wins last, blank
    translation, control chars) plus case-fold conflicts that are valid CSV but
    indicate an authoring mistake. Returns [] when path is None/missing — empty
    list always means "no problems detected", never "could not read".
    """
    if path is None:
        return []
    if not path.exists():
        return [f"Glossary file does not exist: {path}"]
    warnings: list[str] = []
    try:
        with path.open("r", newline="", encoding="utf-8-sig") as fp:
            reader = csv.DictReader(fp)
            fieldnames = reader.fieldnames or []
            if "term" not in fieldnames or "translation" not in fieldnames:
                return [f"Missing required columns 'term'/'translation' (got: {fieldnames})"]
            rows = list(reader)
    except (OSError, csv.Error, UnicodeDecodeError) as exc:
        return [f"Could not read glossary: {exc}"]

    seen_terms: dict[str, int] = {}
    casefold_to_term: dict[str, str] = {}
    for line_no, row in enumerate(rows, start=2):  # +1 for header, +1 for 1-based
        term_raw = row.get("term") or ""
        translation_raw = row.get("translation") or ""
        term = term_raw.strip()
        translation = translation_raw.strip()
        if not term and not translation:
            continue  # blank row, skip
        if term != term_raw:
            warnings.append(f"Line {line_no}: term has leading/trailing whitespace ({term_raw!r})")
        if not term:
            warnings.append(f"Line {line_no}: term is blank")
            continue
        if not translation:
            warnings.append(f"Line {line_no}: term {term!r} has blank translation")
            continue
        if any(ch in translation for ch in ("\n", "\r", "\t")):
            warnings.append(f"Line {line_no}: term {term!r} translation contains control chars (will break prompt formatting)")
        if term in seen_terms:
            warnings.append(f"Line {line_no}: duplicate term {term!r} (first seen at line {seen_terms[term]}, later rows ignored)")
        else:
            seen_terms[term] = line_no
            folded = term.casefold()
            if folded in casefold_to_term and casefold_to_term[folded] != term:
                warnings.append(
                    f"Line {line_no}: term {term!r} differs only in case from "
                    f"{casefold_to_term[folded]!r} — only one will match"
                )
            else:
                casefold_to_term.setdefault(folded, term)
    return warnings


def speaker_name_glossary_from_results(results: list[tuple[str, str, str]]) -> list[tuple[str, str]]:
    actor_names: dict[str, str] = {}
    for source, target, context in results:
        if context != "rpg_maker_actors_name" or not target.strip() or target == source:
            continue
        actor_names[source] = target.strip()

    speaker_aliases: set[str] = set()
    for source, _target, context in results:
        if context != "rpg_maker_event_text":
            continue
        match = _NAMEBOX_RE.search(source)
        if match:
            speaker_aliases.add(match.group(1).strip())

    glossary: dict[str, str] = {}
    for source, translation in actor_names.items():
        aliases = [alias for alias in speaker_aliases if source.endswith(alias)]
        terms = aliases or [source]
        for term in terms:
            if term and term not in glossary:
                glossary[term] = translation
    return list(glossary.items())


def build_auto_glossary(
    results: list[tuple[str, str, str]],
    max_entries: int = 80,
    min_term_len: int = 1,
    max_term_len: int = 40,
) -> list[tuple[str, str]]:
    """Build a glossary from confirmed translations for cross-batch consistency.

    Inspired by RPGMTL's knowledge base: extract name-term pairs from completed
    translations to inject into subsequent LLM batches for terminological consistency.

    Args:
        results: List of (source, target, context) tuples from completed translations.
        max_entries: Maximum number of glossary entries to return.
        min_term_len: Minimum source string length to include (skip very short/generic terms).
        max_term_len: Maximum source string length to include (skip overly long descriptions).

    Returns:
        List of (term, translation) pairs suitable for format_glossary_for_prompt.
    """
    name_contexts = {
        "rpg_maker_actors_name",
        "rpg_maker_actors_nickname",
        "rpg_maker_enemies_name",
        "rpg_maker_skills_name",
        "rpg_maker_items_name",
        "rpg_maker_weapons_name",
        "rpg_maker_armors_name",
        "rpg_maker_states_name",
        "rpg_maker_classes_name",
        "rpg_maker_map_display_name",
        "rpg_maker_system_gameTitle",
        "rpg_maker_system_currencyUnit",
        "rpg_maker_speaker_name",
        "rpg_maker_troops_name",
        "rpg_maker_event_text",
        "rpg_maker_choice",
    }
    glossary: dict[str, str] = {}
    for source, target, context in results:
        if not target or not target.strip() or target == source:
            continue
        if context not in name_contexts:
            continue
        if len(source) < min_term_len or len(source) > max_term_len:
            continue
        if source in glossary:
            continue
        glossary[source] = target.strip()
        if len(glossary) >= max_entries:
            break
    return list(glossary.items())