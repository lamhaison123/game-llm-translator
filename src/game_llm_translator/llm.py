from __future__ import annotations

import json
import os
import re
import time
from abc import ABC, abstractmethod
from typing import Any, Iterable
from urllib.parse import quote_plus

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
}


def _lang_code(language: str | None, default: str = "auto") -> str:
    if not language:
        return default
    normalized = language.strip().lower()
    return LANG_CODES.get(normalized, normalized[:2])

TOKEN_PATTERN = re.compile(
    r"(\\[A-Za-z]+\[[^\]]*\]|\\[A-Za-z]+|\\[{}.$|!><^\\]|%\d+|%[sdfox]|\{[^{}]{1,80}\}|<[^<>]{1,120}>|\[[A-Za-z0-9_]+\]|\$[A-Za-z0-9_]+)"
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


SYSTEM_PROMPT = """You are a professional game localizer.
Translate RPG/visual-novel/game UI text faithfully.
Rules:
- Preserve placeholders, variables, control codes, tags, escape codes.
- Keep line breaks when important.
- Use context_text only for continuity, speaker intent, tone, pronouns, and terminology.
- Do not translate context_text unless it is also the item's source.
- Return strict JSON array only.
- Each output item must contain id, key, and target.
"""


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


def _parse_translation_json(text: str) -> list[dict[str, Any]]:
    text = text.strip()
    if text.startswith("```"):
        text = text.strip("`").removeprefix("json").strip()
    data = json.loads(text)
    if not isinstance(data, list):
        raise ValueError("LLM response must be a JSON array")
    return [item for item in data if isinstance(item, dict) and ("id" in item or "key" in item) and "target" in item]


def _results_from_json(entries: list[TextEntry], text: str) -> list[TranslationResult]:
    data = _parse_translation_json(text)
    by_id = {str(item["id"]): str(item["target"]) for item in data if "id" in item}
    by_key = {str(item["key"]): str(item["target"]) for item in data if "key" in item}
    results: list[TranslationResult] = []
    for entry in entries:
        target = by_id.get(text_identity_id(entry.file, entry.key), by_key.get(entry.key, entry.source))
        results.append(TranslationResult(entry.file, entry.key, entry.source, target, entry.context))
    return results


class LLMProvider(ABC):
    @abstractmethod
    def translate_batch(self, entries: list[TextEntry], target_lang: str, source_lang: str | None = None) -> list[TranslationResult]:
        raise NotImplementedError


class MTLProvider(LLMProvider):
    pause_seconds = 0.2

    def translate_batch(self, entries: list[TextEntry], target_lang: str, source_lang: str | None = None) -> list[TranslationResult]:
        results: list[TranslationResult] = []
        for entry in entries:
            masked_source, tokens = _mask_protected_tokens(entry.source)
            target = self.translate_text(masked_source, target_lang, source_lang)
            target = _restore_protected_tokens(target, tokens)
            results.append(TranslationResult(entry.file, entry.key, entry.source, target or entry.source, entry.context))
            if self.pause_seconds:
                time.sleep(self.pause_seconds)
        return results

    @abstractmethod
    def translate_text(self, text: str, target_lang: str, source_lang: str | None = None) -> str:
        raise NotImplementedError


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
        return "".join(part[0] for part in data[0] if part and part[0])


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
        return "".join(part[0] for part in data[0] if part and part[0])


class AnthropicProvider(LLMProvider):
    def __init__(self, model: str, api_key: str | None = None):
        self.client = Anthropic(api_key=api_key or os.getenv("ANTHROPIC_API_KEY"))
        self.model = model

    def translate_batch(self, entries: list[TextEntry], target_lang: str, source_lang: str | None = None) -> list[TranslationResult]:
        message = self.client.messages.create(
            model=self.model,
            max_tokens=4096,
            thinking={"type": "adaptive"},
            output_config={"effort": "high"},
            system=[{"type": "text", "text": SYSTEM_PROMPT, "cache_control": {"type": "ephemeral"}}],
            messages=[{"role": "user", "content": _user_prompt(entries, target_lang, source_lang)}],
        )
        text = "".join(block.text for block in message.content if getattr(block, "type", None) == "text")
        return _results_from_json(entries, text)


class OpenAIProvider(LLMProvider):
    def __init__(self, model: str, api_key: str | None = None, base_url: str | None = None):
        kwargs: dict[str, str] = {"api_key": api_key or os.getenv("OPENAI_API_KEY") or ""}
        selected_base_url = base_url or os.getenv("OPENAI_BASE_URL") or ""
        if selected_base_url:
            kwargs["base_url"] = selected_base_url
        self.client = OpenAI(**kwargs)
        self.model = model

    def translate_batch(self, entries: list[TextEntry], target_lang: str, source_lang: str | None = None) -> list[TranslationResult]:
        response = self.client.chat.completions.create(
            model=self.model,
            temperature=0.2,
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": _user_prompt(entries, target_lang, source_lang)},
            ],
        )
        text = response.choices[0].message.content or "[]"
        return _results_from_json(entries, text)


def make_provider(provider: str, model: str, api_key: str | None = None, api_base: str | None = None) -> LLMProvider:
    if provider == "anthropic":
        return AnthropicProvider(model, api_key)
    if provider == "openai":
        return OpenAIProvider(model, api_key, api_base)
    if provider in {"openai-compatible", "compatible", "custom-openai", "openrouter", "lmstudio", "ollama"}:
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
