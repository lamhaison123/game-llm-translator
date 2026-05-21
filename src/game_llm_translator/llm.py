from __future__ import annotations

import json
import os
import re
import time
from abc import ABC, abstractmethod
import threading
from dataclasses import dataclass
from typing import Any, Iterable
from urllib.parse import quote_plus
from .app_logging import log_api_call, log_event

import requests

from anthropic import Anthropic
from openai import OpenAI

from .models import TextEntry, TranslationResult, text_identity_id

LANG_CODES = {
    "auto": "auto",
    "english": "en",
    "en": "en",
    "vietnamese": "vi",
    "viet": "vi",
    "vi": "vi",
    "japanese": "ja",
    "ja": "ja",
    "korean": "ko",
    "ko": "ko",
    "chinese": "zh",
    "zh": "zh",
    "french": "fr",
    "fr": "fr",
    "german": "de",
    "de": "de",
    "spanish": "es",
    "es": "es",
    "russian": "ru",
    "ru": "ru",
    "portuguese": "pt",
    "pt": "pt",
    "thai": "th",
    "th": "th",
    "indonesian": "id",
    "id": "id",
    "italian": "it",
    "it": "it",
    "polish": "pl",
    "pl": "pl",
    "turkish": "tr",
    "tr": "tr",
    "arabic": "ar",
    "ar": "ar",
}


def _lang_code(language: str | None, default: str = "auto") -> str:
    if not language:
        return default
    normalized = language.strip().lower()
    if normalized in LANG_CODES:
        return LANG_CODES[normalized]
    if len(normalized) == 2 and normalized.isalpha():
        return normalized
    raise ValueError(f"Unsupported language: {language!r}. Use a known name (e.g. Vietnamese) or ISO code (vi).")

TOKEN_PATTERN = re.compile(
    r"(\\[A-Za-z]+\[[^\]]*\]|\\[A-Za-z]+|\\[{}.$!><^\\]|%\d+|%[sdfox]|\{[^{}]{1,80}\}|<[^<>]{1,120}>|\[[A-Za-z0-9_]+\]|\$[A-Za-z0-9_]+)"
)


def _mask_protected_tokens(text: str) -> tuple[str, dict[str, str]]:
    mapping: dict[str, str] = {}

    def replace(match: re.Match[str]) -> str:
        token = f"ZXQ{len(mapping):04d}QXZ"
        mapping[token] = match.group(0)
        return token

    return TOKEN_PATTERN.sub(replace, text), mapping


def _restore_protected_tokens(text: str, mapping: dict[str, str]) -> str:
    for token, original in mapping.items():
        text = text.replace(token, original)
        text = text.replace(token.lower(), original)
    return text


SYSTEM_PROMPT_BASE = """You are an expert game localizer specializing in RPG, visual novel, and game UI text.

## Output format
- Return a strict JSON array and nothing else. No markdown fences, no explanation, no extra text.
- Each element must have exactly: {"id": "...", "key": "...", "target": "..."} — "id" is REQUIRED (format: file_path + separator + key).
- If you cannot translate an item, copy the source text into target unchanged.

## Translation rules
1. Preserve ALL placeholders, variables, control codes, escape sequences, and tags exactly as-is.
   Examples: \\N[1], \\V[2], \\C[3], %1, %s, {name}, <b>, </b>, \n, \\!, \\., \\|
2. Keep line breaks (\\n, actual newlines) where they appear in the source.
3. Translate naturally for the target language — avoid word-for-word literal translation.
4. Match the register and tone of the source: formal speech stays formal, casual stays casual, dramatic stays dramatic.
5. For character dialogue: use natural spoken language, not written/formal prose.
6. For item/skill names and descriptions: be concise and consistent with RPG terminology.
7. For UI text (menu labels, button text): keep it short and clear.
8. For battle messages: keep them punchy and action-oriented.

## Context usage
- Use context_text only to infer speaker identity, tone, pronouns, and terminology consistency.
- Do NOT translate context_text unless it is also the item's source field.
"""

_LANG_SPECIFIC_RULES: dict[str, str] = {
    "vi": """## Vietnamese-specific rules
- Use natural Vietnamese pronouns appropriate to the character's age/status/relationship.
- Avoid overly formal or stiff phrasing that sounds machine-translated.
- Keep RPG terms consistent throughout the batch (e.g. always use the same word for "skill", "item", "quest").
- Honorifics and address forms should match the character's personality and social role.
""",
    "ja": """## Japanese-specific rules
- Use appropriate keigo level matching the character's social role and relationship.
- Preserve sentence-final particles and speech patterns that define character personality.
- Keep katakana loanwords for modern/foreign concepts; use kanji/kana for traditional RPG terms.
""",
    "zh": """## Chinese-specific rules
- Use Simplified Chinese unless the context clearly calls for Traditional.
- Keep RPG terminology consistent (技能, 物品, 任务, etc.) throughout the batch.
- Match formality level to the character's role and the scene's tone.
""",
    "ko": """## Korean-specific rules
- Use appropriate speech level (존댓말/반말) matching the character's relationship and personality.
- Keep RPG terms consistent throughout the batch.
""",
}


def _build_system_prompt(target_lang: str | None, glossary_block: str = "") -> str:
    lang_key = _lang_code(target_lang, "auto").lower()
    addon = _LANG_SPECIFIC_RULES.get(lang_key, "")
    prompt = SYSTEM_PROMPT_BASE + addon
    if glossary_block:
        prompt = prompt + "\n" + glossary_block
    return prompt


SYSTEM_PROMPT = SYSTEM_PROMPT_BASE  # kept for backward compat with tests


def _user_prompt(entries: Iterable[TextEntry], target_lang: str, source_lang: str | None) -> str:
    payload = [
        {"id": text_identity_id(e.file, e.key), "file": e.file.as_posix(), "key": e.key, "source": e.source, "context": e.context, "context_text": e.context_text}
        for e in entries
    ]
    return json.dumps(
        {
            "task": "translate_game_text",
            "source_language": source_lang or "auto",
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


_INVALID_BACKSLASH_RE = re.compile(r'\\(?!["\\/ \bfnrtu]|u[0-9a-fA-F]{4})')


def _repair_invalid_escapes(text: str) -> str:
    """Double-escape bare backslashes that are not valid JSON escape sequences.

    LLMs sometimes emit RPG Maker codes like \\N[1], \\V[2], \\C[3] as literal
    \\N, \\V, \\C inside a JSON string value, which are invalid JSON escapes.
    This replaces e.g. \\N -> \\\\N so that json.loads can parse the response.
    """
    return _INVALID_BACKSLASH_RE.sub('\\\\\\\\', text)


def _parse_translation_json(text: str) -> tuple[list[dict[str, Any]], ParseStats]:
    text = text.strip()
    if text.startswith("```"):
        text = text.strip("`").removeprefix("json").strip()
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


def _results_from_json(entries: list[TextEntry], text: str) -> tuple[list[TranslationResult], ParseStats]:
    data, stats = _parse_translation_json(text)
    by_id = {str(item["id"]): str(item["target"]) for item in data if "id" in item}
    unique_files = {entry.file.as_posix() for entry in entries}
    by_key: dict[str, str] = {}
    if len(unique_files) == 1:
        by_key = {str(item["key"]): str(item["target"]) for item in data if "key" in item}
    results: list[TranslationResult] = []
    for entry in entries:
        entry_id = text_identity_id(entry.file, entry.key)
        target = by_id.get(entry_id)
        if target is None and by_key:
            target = by_key.get(entry.key)
        if target is None:
            target = entry.source
            stats.fallback += 1
        results.append(TranslationResult(entry.file, entry.key, entry.source, target, entry.context))
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
            results.append(TranslationResult(entry.file, entry.key, entry.source, target or entry.source, entry.context))
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
        params = {
            "client": "gtx",
            "sl": _lang_code(source_lang),
            "tl": _lang_code(target_lang, "vi"),
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
        kwargs: dict[str, Any] = {"api_key": api_key or os.getenv("ANTHROPIC_API_KEY")}
        if base_url:
            kwargs["base_url"] = base_url
        self.client = Anthropic(**kwargs)
        self.model = model

    def translate_batch(self, entries: list[TextEntry], target_lang: str, source_lang: str | None = None) -> list[TranslationResult]:
        self._check_stop()
        system_prompt = _build_system_prompt(target_lang, self.glossary_block)
        max_tokens = min(_estimate_output_tokens(entries), 8192)
        user_prompt = _user_prompt(entries, target_lang, source_lang)
        log_api_call("anthropic", "REQUEST", user_prompt, entry_count=len(entries))
        message = self.client.messages.create(
            model=self.model,
            max_tokens=max_tokens,
            system=[{"type": "text", "text": system_prompt, "cache_control": {"type": "ephemeral"}}],
            messages=[{"role": "user", "content": user_prompt}],
        )
        text = _anthropic_message_text(message)
        log_api_call("anthropic", "RESPONSE", text, entry_count=len(entries))
        if getattr(message, "stop_reason", None) == "max_tokens":
            raise ValueError("Anthropic response truncated (max_tokens); retry with smaller batch")
        results, _stats = _results_from_json(entries, text)
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
        raise ValueError(f"OpenAI-compatible provider returned no message content. finish_reason={getattr(choice, 'finish_reason', getattr(choice, 'finish_reason', '?') if isinstance(choice, dict) else '?')!r}")
    text = _content_text(content)
    if not text.strip():
        raise ValueError(f"OpenAI-compatible provider returned empty message content. finish_reason={getattr(choice, 'finish_reason', '?')!r}")
    return text


class OpenAIProvider(LLMProvider):
    def __init__(self, model: str, api_key: str | None = None, base_url: str | None = None):
        kwargs: dict[str, str] = {"api_key": api_key or os.getenv("OPENAI_API_KEY") or ""}
        selected_base_url = base_url or os.getenv("OPENAI_BASE_URL") or ""
        if selected_base_url:
            kwargs["base_url"] = selected_base_url
        self.client = OpenAI(**kwargs)
        self.model = model

    def translate_batch(self, entries: list[TextEntry], target_lang: str, source_lang: str | None = None) -> list[TranslationResult]:
        self._check_stop()
        system_prompt = _build_system_prompt(target_lang, self.glossary_block)
        user_prompt = _user_prompt(entries, target_lang, source_lang)
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
        results, _stats = _results_from_json(entries, text)
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
