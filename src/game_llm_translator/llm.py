from __future__ import annotations

import json
import os
import re
import time
from abc import ABC, abstractmethod
import threading
from dataclasses import dataclass
from typing import Any, Iterable, cast
from urllib.parse import quote_plus
from .app_logging import log_api_call, log_event

import requests

from anthropic import Anthropic
from openai import OpenAI

from .models import TextEntry, TranslationResult, text_identity_id
# Re-export prompt-related symbols so existing callers (and tests) keep working
# after the split. The prompts module is intentionally LLM-SDK-free.
from .prompts import (
    LANG_CODES,
    SYSTEM_PROMPT,
    SYSTEM_PROMPT_BASE,
    _LANG_SPECIFIC_RULES,
    _NAME_TRANSLATE_SYSTEM,
    _build_system_blocks,
    _build_system_prompt,
    _lang_code,
)


# Matches RPG Maker control codes, format placeholders, and non-namebox angle-bracket tags.
# Namebox angle brackets <Name> are handled separately in _mask_protected_tokens
# so that speaker names inside <...> remain visible to the LLM for translation.
_INNER_CTRL_RE = re.compile(
    r"(\\F[A-Za-z]*\[[^\]]*\]|\\OC\[\d+\]|\\OO\[\d+\]|\\FS\[\d+\]|\\[A-Za-z]+\[[^\]]*\]|\\[A-Za-z]+|\\[{}.$!><^_\\]|%\d+|%[sdfox]|\{[^{}]{1,80}\}|\[[A-Za-z0-9_]+\]|\$[A-Za-z0-9_]+)"
)
# Covers CJK Unified Ideographs Extension A (U+3400-U+4DBF), CJK Unified Ideographs
# (U+4E00-U+9FFF), Hiragana + Katakana (U+3040-U+30FF), and Hangul Syllables (U+AC00-U+D7A3).
_CJK_RE = re.compile(r"[㐀-䶿一-鿿぀-ヿ가-힣]")
# Matches non-namebox angle-bracket tags like <area>, <ItemImage:path>, <N_01>.
# These are NOT YEP_MessageCore nameboxes — they are RPG Maker placeholders that
# must stay fully opaque. A namebox is identified by having control codes (\\n, \\F, etc.)
# before the opening <.
_NON_NAMEBOX_TAG_RE = re.compile(r"<[^<>]{1,120}>")


def _mask_protected_tokens(text: str) -> tuple[str, dict[str, str]]:
    mapping: dict[str, str] = {}

    def replace(match: re.Match[str]) -> str:
        token = f"ZXQ{len(mapping):04d}QXZ"
        mapping[token] = match.group(0)
        return token

    # Phase 1: Detect namebox prefixes (e.g. \n<Name>, \F[N_01]\n<希>, \n<\C[22]フォル>).
    # For nameboxes, we mask only control codes inside <...> but keep the speaker
    # name visible so the LLM can translate it. We also mask control codes before
    # the < and keep < > delimiters visible.
    namebox_match = _NAMEBOX_PREFIX_RE.match(text)
    if namebox_match:
        ctrl_before = namebox_match.group(1)   # e.g. \n or \F[N_01]\n
        name_content = namebox_match.group(2)   # e.g. フォル or \C[22]フォル
        after_namebox = text[namebox_match.end():]

        # Mask control codes before < (e.g. \n, \F[N_01])
        masked_ctrl = _INNER_CTRL_RE.sub(replace, ctrl_before)

        # Mask control codes inside <...> but leave name text visible
        masked_name = _INNER_CTRL_RE.sub(replace, name_content)

        # Reconstruct with < > delimiters visible
        partial = masked_ctrl + "<" + masked_name + ">"

        # Phase 2: Mask remaining tokens in the text after the namebox
        # (and any non-namebox <...> tags in the rest of the text)
        masked_rest = _INNER_CTRL_RE.sub(replace, after_namebox)
        masked_rest = _NON_NAMEBOX_TAG_RE.sub(replace, masked_rest)
        return partial + masked_rest, mapping

    # No namebox prefix — apply all masking patterns
    result = _INNER_CTRL_RE.sub(replace, text)
    result = _NON_NAMEBOX_TAG_RE.sub(replace, result)
    return result, mapping


def _restore_protected_tokens(text: str, mapping: dict[str, str]) -> str:
    sorted_tokens = sorted(mapping.items(), key=lambda kv: len(kv[0]), reverse=True)
    for token, original in sorted_tokens:
        text = text.replace(token, original)
        lower_token = token.lower()
        if lower_token != token:
            text = text.replace(lower_token, original)
    return text


_FIX_BRACKET_RE = re.compile(r'\\(\w+)\s*\[\s*(.*?)\s*\]')
_FIX_ANGLE_RE = re.compile(r'\\(\w+)\s*<\s*(.*?)\s*>')
_FIX_PERCENT_RE = re.compile(r'%\s*(\d+)')
_FIX_BACKSLASH_RE = re.compile(r'\\\s*([{}\$!><\^_\\])')
_NAMEBOX_PREFIX_RE = re.compile(r'^((?:\\[A-Za-z]+\[[^\]]*\]|\\[A-Za-z]+|\\[{}\.$!><\^_\\]|\s)*)<((?:\\[A-Za-z]+\[[^\]]*\]|\\[A-Za-z]+|\\[{}\.$!><\^_\\]|[^<>]){1,80})>')


def extract_namebox_names(entries: list[TextEntry]) -> dict[str, str]:
    """Extract unique CJK namebox speaker names from entries.

    Returns {visible_name: original_content} mapping.
    visible_name = name with control codes stripped (the part to translate).
    original_content = full content inside <...> including control codes.
    """
    names: dict[str, str] = {}
    for entry in entries:
        match = _NAMEBOX_PREFIX_RE.match(entry.source)
        if not match:
            continue
        name_content = match.group(2)
        visible_name = _INNER_CTRL_RE.sub("", name_content).strip()
        if visible_name and _CJK_RE.search(visible_name) and visible_name not in names:
            names[visible_name] = name_content
    return names


def _replace_untranslated_namebox_names(target: str, source: str, name_translations: dict[str, str]) -> str:
    """If the namebox name in target is still CJK/untranslated, replace with translation.

    Skip when target == source (the entry is a fallback — the LLM did not translate
    the dialogue body). Partially replacing only the speaker name would produce a
    misleading "half-translated" entry: the namebox shows the target language while
    the dialogue is still in the source language. Leave such entries fully untranslated
    so they get flagged by the fallback filter and re-translated on the next pass.
    """
    if not name_translations:
        return target
    if target == source:
        return target
    src_match = _NAMEBOX_PREFIX_RE.match(source)
    if not src_match:
        return target
    src_visible = _INNER_CTRL_RE.sub("", src_match.group(2)).strip()
    translation = name_translations.get(src_visible)
    if not translation:
        return target

    tgt_match = _NAMEBOX_PREFIX_RE.match(target)
    if not tgt_match:
        return target
    tgt_visible = _INNER_CTRL_RE.sub("", tgt_match.group(2)).strip()

    # If target name is still the untranslated CJK, replace only the visible name
    if tgt_visible == src_visible and _CJK_RE.search(tgt_visible):
        tgt_name_content = tgt_match.group(2)
        new_name_content = tgt_name_content.replace(tgt_visible, translation)
        return tgt_match.group(1) + "<" + new_name_content + ">" + target[tgt_match.end():]

    return target


def _restore_namebox_prefix(source: str, target: str) -> str:
    """Restore or fix YEP_MessageCore namebox tags in the translated text.

    Handles three cases:
    1. Target has a namebox with a translated name → keep the translated name,
       but restore any control codes from the source namebox that the LLM dropped.
    2. Target has a namebox with the same name → no change needed.
    3. Target dropped the namebox entirely → restore the full source prefix.
    """
    src_match = _NAMEBOX_PREFIX_RE.match(source)
    if not src_match:
        return target
    src_ctrl = src_match.group(1)   # e.g. \n or \F[N_01]\n
    src_name = src_match.group(2)   # e.g. フォル or \C[22]フォル

    tgt_match = _NAMEBOX_PREFIX_RE.match(target)
    if tgt_match:
        # Target has a namebox — the LLM may have translated the name.
        # Restore control codes that the LLM may have dropped from inside <...>.
        tgt_ctrl = tgt_match.group(1)
        tgt_name = tgt_match.group(2)
        # Extract control codes from source name content (e.g. \C[22] from \C[22]フォル)
        src_name_ctrls = [m.group(0) for m in _INNER_CTRL_RE.finditer(src_name)]
        tgt_name_ctrls = [m.group(0) for m in _INNER_CTRL_RE.finditer(tgt_name)]
        if src_name_ctrls and not tgt_name_ctrls:
            # LLM dropped control codes inside <...> — prepend source control codes
            # to the target name, keeping the translated name text.
            tgt_name_text = _INNER_CTRL_RE.sub("", tgt_name)
            restored_name = "".join(src_name_ctrls) + tgt_name_text
            restored_prefix = tgt_ctrl + "<" + restored_name + ">"
            return restored_prefix + target[tgt_match.end():]
        return target

    # Target dropped the namebox entirely — restore the full source prefix.
    return src_match.group(0) + target.lstrip()


def _fix_token_formatting(text: str) -> str:
    """Normalize RPG Maker tokens that LLMs corrupt by adding spaces.

    Examples: \\N [1] -> \\N[1], \\V[ 2 ] -> \\V[2], % 1 -> %1, \\ ! -> \\!
    Inspired by Translator++ fixTranslationFormatting().
    """
    text = _FIX_BRACKET_RE.sub(r'\\\1[\2]', text)
    text = _FIX_ANGLE_RE.sub(r'\\\1<\2>', text)
    text = _FIX_PERCENT_RE.sub(r'%\1', text)
    text = _FIX_BACKSLASH_RE.sub(r'\\\1', text)
    return text


def postprocess_translation(source: str, target: str) -> str:
    target = _fix_token_formatting(target)
    target = _restore_namebox_prefix(source, target)
    return target


_FENCE_LANG_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_+-]*\s*")


def _strip_code_fence(text: str) -> str:
    """Strip surrounding triple-backtick code fence and optional language tag.

    Handles ```json, ``` json (with space), ```JSON, ```Json (mixed case),
    and other language hints like ```python, ```jsonl. Backtick stripping is
    bounded: only leading/trailing runs of backticks are removed.
    """
    if not text.startswith("```"):
        return text
    stripped = text.strip("`").lstrip()
    match = _FENCE_LANG_RE.match(stripped)
    if match:
        stripped = stripped[match.end():]
    return stripped.strip()


def _parse_name_json(text: str, names: dict[str, str]) -> dict[str, str]:
    """Parse LLM response for name translation. Accepts both JSON object and JSON array of items.

    Identity translations (target == source) are kept ONLY for non-CJK source names
    (e.g. "Alice" → "Alice" is valid; "健太" → "健太" means the LLM did not translate).
    This prevents repeated retries for names that are already in the target script.
    """
    text = _strip_code_fence(text.strip())
    text = _repair_mojibake(text)
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        try:
            data = json.loads(_repair_invalid_escapes(text))
        except json.JSONDecodeError:
            return {}

    def _accept(original: str, translated: str) -> bool:
        if not (isinstance(original, str) and isinstance(translated, str)):
            return False
        if original not in names:
            return False
        stripped = translated.strip()
        if not stripped:
            return False
        # Drop identity translations only when source contains CJK — that means the LLM
        # failed to translate. Non-CJK identities (Latin names, already-target script)
        # are legitimate and recording them prevents re-translation each batch.
        if stripped == original and _CJK_RE.search(original):
            return False
        return True

    translations: dict[str, str] = {}
    if isinstance(data, dict):
        for original, translated in data.items():
            if _accept(original, translated):
                translations[original] = translated.strip()
    elif isinstance(data, list):
        # Fallback: LLM returned array of dicts like [{"original": "ディオン", "translation": "Dion"}]
        for item in data:
            if isinstance(item, dict):
                original = item.get("original") or item.get("source") or item.get("name")
                translated = item.get("translation") or item.get("target")
                if _accept(original, translated):
                    translations[original] = translated.strip()
    return translations


def translate_namebox_names(
    provider: LLMProvider,
    names: dict[str, str],
    target_lang: str,
    source_lang: str | None = None,
) -> dict[str, str]:
    """Translate namebox speaker names using a lightweight LLM call.

    Args:
        provider: LLM provider to use for translation.
        names: {visible_name: original_content} from extract_namebox_names.
        target_lang: Target language name/code.
        source_lang: Source language (auto-detected if None).
    Returns:
        {visible_name: translated_name} mapping. Empty dict on failure.
    """
    if not names:
        return {}
    name_list = list(names.keys())
    lang = _lang_code(target_lang, "vi")
    # For MTL providers, translate each name individually
    if isinstance(provider, MTLProvider):
        translations: dict[str, str] = {}
        for name in name_list:
            try:
                translated = provider.translate_text(name, lang, source_lang)
                translated = translated.strip()
                if translated and translated != name:
                    translations[name] = translated
            except RuntimeError:
                raise
            except Exception:
                pass
        return translations
    # For LLM providers, use the specialized name-translation prompt
    system_prompt = _NAME_TRANSLATE_SYSTEM.format(target_lang=lang)
    user_prompt = json.dumps(name_list, ensure_ascii=False)
    truncated = False
    try:
        if isinstance(provider, AnthropicProvider):
            message = provider.client.messages.create(
                model=provider.model,
                max_tokens=min(2048, max(256, len(name_list) * 64)),
                system=[{"type": "text", "text": system_prompt, "cache_control": {"type": "ephemeral"}}],
                messages=[{"role": "user", "content": user_prompt}],
            )
            text = _anthropic_message_text(message)
            truncated = getattr(message, "stop_reason", None) == "max_tokens"
        elif isinstance(provider, OpenAIProvider):
            response = provider.client.chat.completions.create(
                model=provider.model,
                temperature=0.2,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
            )
            text = _chat_completion_text(response)
            choices = getattr(response, "choices", None) or []
            if choices:
                choice = choices[0]
                finish = choice.get("finish_reason") if isinstance(choice, dict) else getattr(choice, "finish_reason", None)
                truncated = finish == "length"
        else:
            # Unknown provider type — fall back to translate_batch (less ideal but functional)
            from .models import TextEntry as _TE
            fake_entries = [_TE(Path("names"), str(i), n, "speaker name") for i, n in enumerate(name_list)]
            results = provider.translate_batch(fake_entries, target_lang, source_lang)
            translations = {}
            for r in results:
                translated = r.target.strip()
                if translated and translated != r.source and r.key.isdigit():
                    idx = int(r.key)
                    if idx < len(name_list):
                        translations[name_list[idx]] = translated
            return translations
        parsed = _parse_name_json(text, names)
        if truncated:
            # Truncated response may still contain valid prefix entries — surface a warning
            # so the user can raise max_tokens or shrink batches, but don't discard the
            # partial result the way `raise` + `except Exception: return {}` would.
            log_event(
                f"WARN: namebox name translation truncated by provider "
                f"(got {len(parsed)} of {len(name_list)} names; consider smaller batches)"
            )
        return parsed
    except RuntimeError:
        raise
    except Exception:
        return {}


_CONTEXT_HINTS: dict[str, str] = {
    "rpg_maker_event_text": "dialogue",
    "rpg_maker_choice": "choice option",
    "rpg_maker_map_display_name": "map name",
    "rpg_maker_skills_name": "skill name",
    "rpg_maker_skills_description": "skill description",
    "rpg_maker_skills_message1": "battle message",
    "rpg_maker_skills_message2": "battle message",
    "rpg_maker_items_name": "item name",
    "rpg_maker_items_description": "item description",
    "rpg_maker_weapons_name": "weapon name",
    "rpg_maker_weapons_description": "weapon description",
    "rpg_maker_armors_name": "armor name",
    "rpg_maker_armors_description": "armor description",
    "rpg_maker_actors_name": "actor name",
    "rpg_maker_actors_nickname": "actor nickname",
    "rpg_maker_actors_profile": "actor profile",
    "rpg_maker_enemies_name": "enemy name",
    "rpg_maker_states_name": "state name",
    "rpg_maker_states_description": "state description",
    "rpg_maker_states_message1": "state message",
    "rpg_maker_states_message2": "state message",
    "rpg_maker_states_message3": "state message",
    "rpg_maker_states_message4": "state message",
    "rpg_maker_classes_name": "class name",
    "rpg_maker_system_gameTitle": "game title",
    "rpg_maker_system_currencyUnit": "currency unit",
    "rpg_maker_terms_basic": "term",
    "rpg_maker_terms_commands": "command",
    "rpg_maker_terms_params": "parameter name",
    "rpg_maker_terms_messages": "system message",
    "rpg_maker_speaker_name": "speaker name",
    "rpg_maker_troops_name": "troop name",
    "rpg_maker_mz_plugin_text": "plugin text",
    "rpg_maker_system_elements": "element name",
    "rpg_maker_system_weaponTypes": "weapon type",
    "rpg_maker_system_armorTypes": "armor type",
    "rpg_maker_system_equipTypes": "equip type",
    "rpg_maker_system_skillTypes": "skill type",
}


def _context_hint(context: str) -> str:
    if context.startswith("rpg_maker_plugin_ui_"):
        return "plugin UI text"
    prefix = context.split("_plugin_")[0] if "_plugin_" in context else context
    hint = _CONTEXT_HINTS.get(prefix)
    if hint:
        return hint
    if context.startswith("rpg_maker_mz_plugin_"):
        return "plugin text"
    if "_plugin_" in context:
        return "plugin text"
    return context


def _user_prompt(entries: Iterable[TextEntry], target_lang: str, source_lang: str | None) -> str:
    payload = [
        {
            "id": text_identity_id(e.file, e.key),
            "file": e.file.as_posix(),
            "key": e.key,
            "source": e.source,
            "context": _context_hint(e.context),
            "context_text": e.context_text,
        }
        for e in entries
    ]
    source_label = source_lang or "auto"
    if source_label == "auto":
        for item in payload:
            src = item["source"]
            if any('\u3040' <= c <= '\u30ff' for c in src):
                source_label = "ja"
                break
            if any('\uac00' <= c <= '\ud7af' for c in src):
                source_label = "ko"
                break
            if any('\u4e00' <= c <= '\u9fff' for c in src):
                source_label = "zh"
                break
    return json.dumps(
        {
            "task": "translate_game_text",
            "source_language": source_label,
            "target_language": target_lang,
            "items": payload,
        },
        ensure_ascii=False,
    )


def _response_preview(text: str, limit: int = 300) -> str:
    compact = text.replace("\r", "\\r").replace("\n", "\\n")
    return compact[:limit]


@dataclass(slots=True)
class ParseStats:
    parsed: int = 0
    skipped: int = 0
    fallback: int = 0


_INVALID_BACKSLASH_RE = re.compile(r'\\(?!["\\/bfnrtu]|u[0-9a-fA-F]{4})')

_MOJIBAKE_RE = re.compile(r'[\xc0-\xdf][\x80-\xbf]|[\xe0-\xef][\x80-\xbf]{2}|[\xf0-\xf7][\x80-\xbf]{3}')


def _repair_mojibake(text: str) -> str:
    """Fix UTF-8 text that was incorrectly decoded as Latin-1 by the server.

    Some OpenAI-compatible servers return UTF-8 bytes but the response is
    treated as Latin-1, producing sequences like 'TÃ´i' instead of 'Tôi'.
    Detect this by checking for Latin-1 multibyte sequences and re-encode.
    """
    if not _MOJIBAKE_RE.search(text):
        return text
    try:
        return text.encode("latin-1").decode("utf-8")
    except (UnicodeEncodeError, UnicodeDecodeError):
        return text


def _repair_invalid_escapes(text: str) -> str:
    """Double-escape bare backslashes that are not valid JSON escape sequences.

    LLMs sometimes emit RPG Maker codes like \\N[1], \\V[2], \\C[3] as literal
    \\N, \\V, \\C inside a JSON string value, which are invalid JSON escapes.
    This replaces e.g. \\N -> \\\\N so that json.loads can parse the response.
    """
    return _INVALID_BACKSLASH_RE.sub(r'\\\\', text)


def _parse_translation_json(text: str) -> tuple[list[dict[str, Any]], ParseStats]:
    text = text.strip()
    if text.startswith("```"):
        text = text.strip("`").removeprefix("json").strip()
    text = _repair_mojibake(text)
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        try:
            data = json.loads(_repair_invalid_escapes(text))
        except json.JSONDecodeError as exc:
            raise ValueError(f"LLM response was not valid JSON: {exc}. Preview: {_response_preview(text)}") from exc
    if not isinstance(data, list):
        raise ValueError("LLM response must be a JSON array")
    items: list[dict[str, Any]] = []
    skipped = 0
    for item in data:
        if not isinstance(item, dict) or not ("id" in item or "key" in item) or "target" not in item:
            skipped += 1
            continue
        if item["target"] is None:
            skipped += 1
            continue
        row = dict(item)
        row["target"] = str(row["target"])
        items.append(row)
    return items, ParseStats(parsed=len(items), skipped=skipped)


def _estimate_output_tokens(entries: list[TextEntry]) -> int:
    chars = sum(len(e.source) + len(e.context_text) for e in entries)
    return max(1024, min(int(chars * 1.5) + 512, 16384))


def _mask_entries(entries: list[TextEntry]) -> tuple[list[TextEntry], list[dict[str, str]]]:
    """Return copies of entries with protected tokens masked, plus per-entry token maps."""
    masked_entries: list[TextEntry] = []
    token_maps: list[dict[str, str]] = []
    for entry in entries:
        masked_source, mapping = _mask_protected_tokens(entry.source)
        token_maps.append(mapping)
        if mapping:
            masked_entries.append(TextEntry(entry.file, entry.key, masked_source, entry.context, entry.context_text, entry.sub_keys))
        else:
            masked_entries.append(entry)
    return masked_entries, token_maps


def _results_from_json(
    entries: list[TextEntry],
    text: str,
    token_maps: list[dict[str, str]] | None = None,
) -> tuple[list[TranslationResult], ParseStats]:
    data, stats = _parse_translation_json(text)
    by_id = {str(item["id"]): str(item["target"]) for item in data if "id" in item}
    unique_files = {entry.file.as_posix() for entry in entries}
    by_key: dict[str, str] = {}
    if len(unique_files) == 1:
        by_key = {str(item["key"]): str(item["target"]) for item in data if "key" in item}
    results: list[TranslationResult] = []
    for i, entry in enumerate(entries):
        entry_id = text_identity_id(entry.file, entry.key)
        target = by_id.get(entry_id)
        if target is None and by_key:
            target = by_key.get(entry.key)
        if target is None:
            target = entry.source
            stats.fallback += 1
        if token_maps and i < len(token_maps) and token_maps[i]:
            target = _restore_protected_tokens(target, token_maps[i])
        target = postprocess_translation(entry.source, target)
        results.append(TranslationResult(entry.file, entry.key, entry.source, target, entry.context, sub_keys=entry.sub_keys))
    return results, stats


class LLMProvider(ABC):
    glossary_block: str = ""
    stop_event: threading.Event | None = None

    def set_glossary(self, glossary_block: str) -> None:
        self.glossary_block = glossary_block or ""

    def _check_stop(self) -> None:
        if self.stop_event is not None and self.stop_event.is_set():
            raise RuntimeError("Stopped by user")

    def _sleep_interruptible(self, seconds: float) -> None:
        if seconds <= 0:
            return
        end = time.monotonic() + seconds
        while time.monotonic() < end:
            self._check_stop()
            time.sleep(min(0.25, max(0.0, end - time.monotonic())))

    @abstractmethod
    def translate_batch(self, entries: list[TextEntry], target_lang: str, source_lang: str | None = None) -> list[TranslationResult]:
        raise NotImplementedError


class MTLProvider(LLMProvider):
    pause_seconds = 0.2

    def translate_batch(self, entries: list[TextEntry], target_lang: str, source_lang: str | None = None) -> list[TranslationResult]:
        results: list[TranslationResult] = []
        for entry in entries:
            self._check_stop()
            masked_source, tokens = _mask_protected_tokens(entry.source)
            target = self.translate_text(masked_source, target_lang, source_lang)
            target = _restore_protected_tokens(target, tokens)
            target = postprocess_translation(entry.source, target)
            results.append(TranslationResult(entry.file, entry.key, entry.source, target or entry.source, entry.context, sub_keys=entry.sub_keys))
            if self.pause_seconds:
                self._sleep_interruptible(self.pause_seconds)
        return results

    @abstractmethod
    def translate_text(self, text: str, target_lang: str, source_lang: str | None = None) -> str:
        raise NotImplementedError


def _parse_google_translate_response(data: Any) -> str:
    if not isinstance(data, list) or not data or not isinstance(data[0], list):
        return ""
    return "".join(part[0] for part in data[0] if isinstance(part, list) and part and isinstance(part[0], str))


class GoogleMTLProvider(MTLProvider):
    def translate_text(self, text: str, target_lang: str, source_lang: str | None = None) -> str:
        lang = _lang_code(target_lang, "vi")
        params = {
            "client": "gtx",
            "sl": _lang_code(source_lang),
            "tl": lang,
            "dt": "t",
            "q": text,
        }
        response = requests.get("https://translate.googleapis.com/translate_a/single", params=params, timeout=30)
        response.raise_for_status()
        data = response.json()
        return _parse_google_translate_response(data)


class MyMemoryMTLProvider(MTLProvider):
    pause_seconds = 0.5

    def translate_text(self, text: str, target_lang: str, source_lang: str | None = None) -> str:
        source = _lang_code(source_lang, "auto")
        if source == "auto":
            source = "en"
        langpair = f"{source}|{_lang_code(target_lang, 'vi')}"
        response = requests.get(
            "https://api.mymemory.translated.net/get",
            params={"q": text, "langpair": langpair},
            timeout=30,
        )
        response.raise_for_status()
        data = response.json()
        return str(data.get("responseData", {}).get("translatedText", ""))


class LibreTranslateMTLProvider(MTLProvider):
    pause_seconds = 0.5

    def __init__(self):
        self.endpoint = os.getenv("LIBRETRANSLATE_URL", "https://libretranslate.com/translate")
        self.api_key = os.getenv("LIBRETRANSLATE_API_KEY", "")

    def translate_text(self, text: str, target_lang: str, source_lang: str | None = None) -> str:
        payload = {
            "q": text,
            "source": _lang_code(source_lang),
            "target": _lang_code(target_lang, "vi"),
            "format": "text",
        }
        if self.api_key:
            payload["api_key"] = self.api_key
        response = requests.post(self.endpoint, json=payload, timeout=30)
        response.raise_for_status()
        return str(response.json().get("translatedText", ""))


class MicrosoftMTLProvider(MTLProvider):
    def __init__(self):
        self.api_key = os.getenv("MICROSOFT_TRANSLATOR_KEY", "")
        self.region = os.getenv("MICROSOFT_TRANSLATOR_REGION", "")
        self.endpoint = os.getenv("MICROSOFT_TRANSLATOR_ENDPOINT", "https://api.cognitive.microsofttranslator.com")
        if not self.api_key:
            raise ValueError("MICROSOFT_TRANSLATOR_KEY is required for provider 'bing'/'microsoft'")

    def translate_text(self, text: str, target_lang: str, source_lang: str | None = None) -> str:
        params = {"api-version": "3.0", "to": _lang_code(target_lang, "vi")}
        source = _lang_code(source_lang)
        if source != "auto":
            params["from"] = source
        headers = {
            "Ocp-Apim-Subscription-Key": self.api_key,
            "Content-Type": "application/json",
        }
        if self.region:
            headers["Ocp-Apim-Subscription-Region"] = self.region
        response = requests.post(
            f"{self.endpoint.rstrip('/')}/translate",
            params=params,
            headers=headers,
            json=[{"text": text}],
            timeout=30,
        )
        response.raise_for_status()
        data = response.json()
        return str(data[0]["translations"][0]["text"])


class YandexMTLProvider(MTLProvider):
    def __init__(self):
        self.api_key = os.getenv("YANDEX_TRANSLATE_API_KEY", "")
        self.folder_id = os.getenv("YANDEX_FOLDER_ID", "")
        if not self.api_key or not self.folder_id:
            raise ValueError("YANDEX_TRANSLATE_API_KEY and YANDEX_FOLDER_ID are required for provider 'yandex'")

    def translate_text(self, text: str, target_lang: str, source_lang: str | None = None) -> str:
        body = {
            "targetLanguageCode": _lang_code(target_lang, "vi"),
            "texts": [text],
            "folderId": self.folder_id,
        }
        source = _lang_code(source_lang)
        if source != "auto":
            body["sourceLanguageCode"] = source
        response = requests.post(
            "https://translate.api.cloud.yandex.net/translate/v2/translate",
            headers={"Authorization": f"Api-Key {self.api_key}"},
            json=body,
            timeout=30,
        )
        response.raise_for_status()
        data = response.json()
        return str(data["translations"][0]["text"])


class GoogleWebMTLProvider(MTLProvider):
    def translate_text(self, text: str, target_lang: str, source_lang: str | None = None) -> str:
        url = "https://translate.googleapis.com/translate_a/single?client=gtx"
        url += f"&sl={quote_plus(_lang_code(source_lang))}&tl={quote_plus(_lang_code(target_lang, 'vi'))}&dt=t&q={quote_plus(text)}"
        response = requests.get(url, timeout=30)
        response.raise_for_status()
        data = response.json()
        return _parse_google_translate_response(data)


def _content_text(content: Any) -> str:
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    if isinstance(content, dict):
        # Skip thinking blocks (Claude extended thinking)
        if content.get("type") in ("thinking", "redacted_thinking"):
            return ""
        if content.get("type") == "text" and content.get("text") is not None:
            return str(content["text"])
        if content.get("content") is not None:
            return _content_text(content.get("content"))
        return ""
    if isinstance(content, list):
        return "".join(_content_text(item) for item in content)
    # Object with .type attribute
    block_type = getattr(content, "type", None)
    if block_type in ("thinking", "redacted_thinking"):
        return ""
    if block_type == "text" and getattr(content, "text", None) is not None:
        return str(getattr(content, "text"))
    if getattr(content, "content", None) is not None:
        return _content_text(getattr(content, "content"))
    return ""


def _anthropic_message_text(message: Any) -> str:
    if message is None:
        raise ValueError("Anthropic-compatible provider returned no message")
    content = getattr(message, "content", None)
    if content is None:
        raise ValueError("Anthropic-compatible provider returned no content")
    text = _content_text(content)
    if not text.strip():
        raise ValueError("Anthropic-compatible provider returned empty text content")
    return text


class AnthropicProvider(LLMProvider):
    def __init__(self, model: str, api_key: str | None = None, base_url: str | None = None):
        kwargs: dict[str, Any] = {"api_key": api_key or os.getenv("ANTHROPIC_API_KEY") or ""}
        if base_url:
            kwargs["base_url"] = base_url
        self.client = Anthropic(**kwargs)
        self.model = model

    def translate_batch(self, entries: list[TextEntry], target_lang: str, source_lang: str | None = None) -> list[TranslationResult]:
        self._check_stop()
        masked_entries, token_maps = _mask_entries(entries)
        stable_block, glossary_block = _build_system_blocks(target_lang, self.glossary_block)
        max_tokens = min(_estimate_output_tokens(masked_entries), 8192)
        user_prompt = _user_prompt(masked_entries, target_lang, source_lang)
        log_api_call("anthropic", "REQUEST", user_prompt, entry_count=len(entries))
        # Split system into two blocks so the large stable block (BASE+lang rules)
        # gets a stable cache key while the per-batch glossary does not invalidate it.
        system: list[dict[str, Any]] = [
            {"type": "text", "text": stable_block, "cache_control": {"type": "ephemeral"}}
        ]
        if glossary_block:
            system.append({"type": "text", "text": glossary_block})
        message = self.client.messages.create(
            model=self.model,
            max_tokens=max_tokens,
            system=system,
            messages=[{"role": "user", "content": user_prompt}],
        )
        text = _anthropic_message_text(message)
        log_api_call("anthropic", "RESPONSE", text, entry_count=len(entries))
        if getattr(message, "stop_reason", None) == "max_tokens":
            raise ValueError("Anthropic response truncated (max_tokens); retry with smaller batch")
        results, _stats = _results_from_json(masked_entries, text, token_maps)
        return results


def _chat_completion_text(response: Any) -> str:
    choices = getattr(response, "choices", None)
    if not choices:
        raise ValueError("OpenAI-compatible provider returned no choices")
    choice = choices[0]
    if choice is None:
        raise ValueError("OpenAI-compatible provider returned empty choice")
    message = choice.get("message") if isinstance(choice, dict) else getattr(choice, "message", None)
    if message is None:
        raise ValueError("OpenAI-compatible provider returned no message")
    # Try content first, then reasoning_content fallback (some providers put output there)
    if isinstance(message, dict):
        content = message.get("content") or message.get("reasoning_content") or message.get("text")
    else:
        content = getattr(message, "content", None) or getattr(message, "reasoning_content", None) or getattr(message, "text", None)
    if content is None:
        finish = choice.get("finish_reason") if isinstance(choice, dict) else getattr(choice, "finish_reason", "?")
        raise ValueError(f"OpenAI-compatible provider returned no message content. finish_reason={finish!r}")
    text = _content_text(content)
    if not text.strip():
        raise ValueError(f"OpenAI-compatible provider returned empty message content. finish_reason={getattr(choice, 'finish_reason', '?')!r}")
    return text


class OpenAIProvider(LLMProvider):
    def __init__(self, model: str, api_key: str | None = None, base_url: str | None = None):
        kwargs = cast(Any, {"api_key": api_key or os.getenv("OPENAI_API_KEY") or "sk-placeholder"})
        selected_base_url = base_url or os.getenv("OPENAI_BASE_URL") or ""
        if selected_base_url:
            kwargs["base_url"] = selected_base_url
        self.client = OpenAI(**kwargs)
        self.model = model

    def translate_batch(self, entries: list[TextEntry], target_lang: str, source_lang: str | None = None) -> list[TranslationResult]:
        self._check_stop()
        masked_entries, token_maps = _mask_entries(entries)
        system_prompt = _build_system_prompt(target_lang, self.glossary_block)
        user_prompt = _user_prompt(masked_entries, target_lang, source_lang)
        log_api_call("openai", "REQUEST", user_prompt, entry_count=len(entries))
        response = self.client.chat.completions.create(
            model=self.model,
            temperature=0.2,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
        )
        try:
            text = _chat_completion_text(response)
        except ValueError as exc:
            try:
                raw = response.model_dump_json() if hasattr(response, "model_dump_json") else str(response)
            except Exception:
                raw = str(response)
            log_api_call("openai", "RESPONSE_ERROR", raw[:2000], entry_count=len(entries))
            raise ValueError(f"{exc} | raw={raw[:500]}") from exc
        log_api_call("openai", "RESPONSE", text, entry_count=len(entries))
        choices = getattr(response, "choices", None) or []
        if choices:
            choice = choices[0]
            finish = choice.get("finish_reason") if isinstance(choice, dict) else getattr(choice, "finish_reason", None)
            if finish == "length":
                raise ValueError("OpenAI response truncated (finish_reason=length); retry with smaller batch")
        results, _stats = _results_from_json(masked_entries, text, token_maps)
        return results


def make_provider(provider: str, model: str, api_key: str | None = None, api_base: str | None = None) -> LLMProvider:
    provider = provider.strip().lower()
    if provider == "anthropic":
        return AnthropicProvider(model, api_key)
    if provider in {"anthropic-compatible", "anthropic compatible", "custom-anthropic"}:
        return AnthropicProvider(
            model,
            api_key or os.getenv("ANTHROPIC_COMPATIBLE_API_KEY") or os.getenv("ANTHROPIC_API_KEY"),
            api_base or os.getenv("ANTHROPIC_COMPATIBLE_BASE_URL"),
        )
    if provider == "openai":
        return OpenAIProvider(model, api_key, api_base)
    if provider in {"openai-compatible", "openai compatible", "compatible", "custom-openai", "openrouter", "lmstudio", "ollama"}:
        return OpenAIProvider(model, api_key or os.getenv("OPENAI_COMPATIBLE_API_KEY"), api_base or os.getenv("OPENAI_COMPATIBLE_BASE_URL"))
    if provider in {"google", "google-mtl", "google-web"}:
        return GoogleMTLProvider()
    if provider in {"mymemory", "memory"}:
        return MyMemoryMTLProvider()
    if provider in {"libre", "libretranslate"}:
        return LibreTranslateMTLProvider()
    if provider in {"bing", "microsoft", "azure"}:
        return MicrosoftMTLProvider()
    if provider == "yandex":
        return YandexMTLProvider()
    raise ValueError(f"Unsupported provider: {provider}")
