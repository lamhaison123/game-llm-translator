from __future__ import annotations

import re

from .llm import _INNER_CTRL_RE, _NON_NAMEBOX_TAG_RE, _NAMEBOX_PREFIX_RE
from .models import TranslationResult

_PLACEHOLDER_RE = re.compile(
    _INNER_CTRL_RE.pattern + "|" + _NON_NAMEBOX_TAG_RE.pattern
)

_NAME_CONTEXTS = frozenset({
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
})

_SHORT_UI_CONTEXTS = frozenset({
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
    "rpg_maker_terms_commands",
    "rpg_maker_terms_basic",
    "rpg_maker_terms_params",
    "rpg_maker_choice",
    "rpg_maker_system_gameTitle",
    "rpg_maker_system_currencyUnit",
    "rpg_maker_speaker_name",
    "rpg_maker_troops_name",
    "rpg_maker_system_elements",
    "rpg_maker_system_weaponTypes",
    "rpg_maker_system_armorTypes",
    "rpg_maker_system_equipTypes",
    "rpg_maker_system_skillTypes",
})

_DESCRIPTION_CONTEXTS = frozenset({
    "rpg_maker_skills_description",
    "rpg_maker_items_description",
    "rpg_maker_weapons_description",
    "rpg_maker_armors_description",
    "rpg_maker_states_description",
    "rpg_maker_actors_profile",
})


def translation_warnings(source: str, target: str, context: str = "") -> list[str]:
    """Return non-fatal quality warnings for a source/target pair."""
    if not target.strip() or target == source:
        return []
    warnings: list[str] = []
    for match in _PLACEHOLDER_RE.finditer(source):
        token = match.group(0)
        if token not in target:
            warnings.append(f"missing placeholder {token!r}")
    if _NAMEBOX_PREFIX_RE.match(source) and not _NAMEBOX_PREFIX_RE.match(target):
        warnings.append("missing YEP_MessageCore namebox prefix")
    src_newlines = source.count("\n")
    tgt_newlines = target.count("\n")
    if src_newlines and tgt_newlines != src_newlines:
        warnings.append(f"newline count mismatch ({src_newlines} vs {tgt_newlines})")
    src_cjk = sum(1 for c in source if '\u4e00' <= c <= '\u9fff' or '\u3040' <= c <= '\u30ff' or '\uac00' <= c <= '\ud7a3')
    cjk_ratio = src_cjk / max(len(source), 1)
    if context in _SHORT_UI_CONTEXTS:
        max_ratio = 4 if cjk_ratio >= 0.5 else 2.5
        if len(target) > len(source) * max_ratio and len(source) < 60:
            warnings.append(f"name/UI label too long ({len(source)}→{len(target)} chars, max {max_ratio}x for context '{context}')")
    elif context in _DESCRIPTION_CONTEXTS:
        if len(target) > len(source) * 4 and len(source) < 120:
            warnings.append(f"description too long ({len(source)}→{len(target)} chars, max 4x for context '{context}')")
    elif cjk_ratio >= 0.5 and len(source) <= 6:
        pass
    else:
        max_ratio = 8 if (cjk_ratio >= 0.5 and len(source) <= 10) else 3
        if len(target) > len(source) * max_ratio and len(source) < 120:
            warnings.append("translation much longer than source (possible UI overflow)")
    return warnings


def check_noun_consistency(results: list[TranslationResult]) -> list[tuple[str, list[str]]]:
    """Find source texts (likely nouns/names) that are translated inconsistently.

    Groups results by normalized source text and context category (name/term vs
    dialogue). Returns a list of (source_text, [different_translations]) tuples
    for sources that appear 2+ times with 2+ distinct translations in name contexts.
    """
    name_groups: dict[str, dict[str, list[str]]] = {}
    for r in results:
        if not r.target.strip() or r.target == r.source:
            continue
        if r.context not in _NAME_CONTEXTS:
            continue
        src = r.source.strip()
        if not src or len(src) > 80:
            continue
        tgt = r.target.strip()
        group = name_groups.setdefault(src, {})
        group.setdefault(tgt, []).append(r.key)

    inconsistencies: list[tuple[str, list[str]]] = []
    for src, translations in sorted(name_groups.items()):
        if len(translations) >= 2:
            inconsistencies.append((src, list(translations.keys())))
    return inconsistencies


def format_noun_warnings(inconsistencies: list[tuple[str, list[str]]], max_items: int = 30) -> list[str]:
    """Format noun consistency warnings as human-readable log messages."""
    warnings: list[str] = []
    for src, targets in inconsistencies[:max_items]:
        targets_str = " | ".join(targets)
        warnings.append(f"Noun inconsistency: '{src}' translated as: [{targets_str}]")
    remaining = len(inconsistencies) - max_items
    if remaining > 0:
        warnings.append(f"...and {remaining} more noun inconsistencies")
    return warnings
