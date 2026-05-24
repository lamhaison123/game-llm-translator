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

# Matches RPG Maker control codes, format placeholders, and non-namebox angle-bracket tags.
# Namebox angle brackets <Name> are handled separately in _mask_protected_tokens
# so that speaker names inside <...> remain visible to the LLM for translation.
_INNER_CTRL_RE = re.compile(
    r"(\\F[A-Za-z]*\[[^\]]*\]|\\OC\[\d+\]|\\OO\[\d+\]|\\FS\[\d+\]|\\[A-Za-z]+\[[^\]]*\]|\\[A-Za-z]+|\\[{}.$!><^_\\]|%\d+|%[sdfox]|\{[^{}]{1,80}\}|\[[A-Za-z0-9_]+\]|\$[A-Za-z0-9_]+)"
)
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


SYSTEM_PROMPT_BASE = """You are an expert game localizer specializing in RPG Maker and visual novel text translation.

## Output format
- Return a strict JSON array and nothing else. No markdown fences, no explanation, no extra text.
- Each element must have exactly: {"id": "...", "key": "...", "target": "..."} — "id" is REQUIRED (format: file_path + separator + key).
- If you cannot translate an item, copy the source text into target unchanged.
- Return items in the SAME ORDER as the input.

## RPG Maker control codes — ALWAYS preserve exactly
These tokens MUST appear in the target text unchanged. Never translate, remove, reorder, or add spaces inside them:
- \\N[n] — Actor name substitution (e.g. \\N[1] = actor 1's name). CRITICAL: LLMs frequently corrupt this to \\N [1] or \\N[ 1]. Always output as \\N[n] with no spaces.
- \\P[n] — Party member name substitution (e.g. \\P[1] = first party member). Same no-space rule.
- \\V[n] — Variable value substitution.
- \\C[n] — Text color change. \\C[0] resets to default.
- \\I[n] — Icon index display. The icon and adjacent text form a single phrase; keep them together.
- \\G — Currency unit display.
- \\! — Wait for user input. \\. — Short wait (1/4 second). \\| — Long wait (1 second).
- \\{ — Increase text size. \\} — Decrease text size.
- \\$ — Open gold window.
- \\/ — Escape backslash (produces a single \\).
- \\_ — Half-width space (rare, mostly MZ).
- \\F[name] — Standing picture expression code (MZ plugin: LL_StandingPicture, etc.). Preserve bracket contents exactly.
- \\FFF[name] — Standing picture face code. Preserve exactly.
- \\FH[ON/OFF] — Standing picture highlight toggle. Preserve exactly.
- \\OC[n] — Outline color (MZ). \\OO[n] — Outline opacity (MZ). \\FS[n] — Font size (MZ).
- %1, %2, %3… — Positional parameter substitution in System.json messages. %1 is usually the actor name, %2 is the target/skill name.
- {name}, {0}, %s, %d — Other common placeholders.

## YEP_MessageCore namebox pattern
Some RPG Maker MV/MZ games use the Yanfly MessageCore plugin to display speaker names in a name box window.
- Pattern: the dialogue line STARTS with \\n<Name> or a control-code prefix followed by \\n<Name>, e.g. \\F[N_01]\\n<希> or \\C[27]\\n<彩> or just \\n<Alice>.
- The text inside <...> is the speaker name displayed in the name box — translate it like any speaker name.
- Keep ALL control codes BEFORE the <Name> tag unchanged (they style the name box appearance).
- If the source has a namebox prefix and the translation drops it, the in-game name window will disappear — ALWAYS preserve it.

## Translation rules
1. Preserve ALL placeholders, variables, control codes, escape sequences, and tags exactly as-is.
   NEVER reorder, remove, or modify these tokens. Keep them in their logical position in the target language sentence.
2. Keep line breaks (\\n or actual newlines in JSON strings) exactly where they appear in the source. Do not merge or split lines.
   Exception: when a dialogue block is merged (source contains newlines from multiple consecutive Show Text lines), preserve the same number of line breaks in the translation. Each \\n corresponds to a separate in-game text line.
3. Translate naturally for the target language — avoid word-for-word literal translation. Reorder grammar naturally while keeping all tokens.
4. Match the register and tone of the source: formal speech stays formal, casual stays casual, dramatic stays dramatic.
5. For character dialogue: use natural spoken language, not written/formal prose. Respect character voice indicated by context_text speaker tags like [SpeakerName].
6. For item/skill names and descriptions: be concise and consistent with RPG terminology. Names should fit UI slots (typically 12-20 characters for CJK → alphabetic translations).
7. For UI text (menu labels, button text, commands): keep it short and clear. Use standard gaming terminology for the target language.
8. For battle messages: keep them punchy and action-oriented. %1 is typically the actor/enemy name, %2 is the skill/item name.
9. For skill/item descriptions containing \\i[N] icon tokens: the icon token and the text after it form a single phrase — translate the text but keep the icon token in place.
10. For speaker names (context "speaker name"): translate character names consistently across ALL items in the entire batch. If a name appears in both Actors.json and dialogue, use the SAME translation.
11. For state names, descriptions, and messages: keep them very brief. State names like "戦闘不能" → "KO" or "Knocked Out" (not "Unable to Battle"). State messages use %1 for the affected battler's name.
12. For "note" fields containing angle-bracket plugin notetags (e.g. <ItemImage:path>, <バトルウェイト:10>): do NOT translate the tag name or its parameter if it is a number/asset path — only translate any descriptive text within the note that is player-facing. Most note tags are engine configuration and should be copied unchanged.
13. For MZ plugin command text (context starts with "rpg_maker_mz_plugin_"): only translate string values that contain natural language. Numeric IDs, asset paths, and identifier strings (e.g. actorId, switchId) are engine configuration — copy them unchanged. When in doubt, copy unchanged.
14. For System.json array entries (armorTypes, elements, equipTypes, skillTypes, weaponTypes): these are short UI labels in menus. Translate them with standard RPG terminology, keeping each entry concise. Some MZ games have per-character equipment types (e.g. "アキナ専用" = "Akina-only") — translate the descriptive word but keep character names consistent with speaker names.
15. For plugin UI text (context "plugin UI text"): these are short labels from custom plugin overlays like phone-menu apps, galleries, or shop UIs. Keep translations very short (2-4 words ideal). Format strings like %1, %2 in plugin UI are runtime substitution markers, not RPG Maker control codes — preserve them exactly.
16. For already-partially-translated games: when a source string mixes CJK characters with target-language text (e.g. Japanese + Vietnamese), the target-language portion is likely an existing partial translation. Keep existing target-language text consistent; only translate the remaining CJK portions. Do NOT re-translate already-translated segments.

## Context usage
- The "context" field describes the type of text. Common values:
  dialogue = in-game dialogue line, choice option = player choice, speaker name = character name,
  map name = area name, skill/item/weapon/armor/enemy/state name = database name,
  skill/item/weapon/armor/state description = database description, battle message = battle log text,
  term/command/parameter name = system UI label, system message = engine message template,
  plugin text = plugin command text, plugin UI text = custom plugin overlay label/button,
  element/weapon type/armor type/equip type/skill type = system menu category label,
  currency unit = money unit, game title = game title,
  actor name/nickname/profile = character database fields, troop name = enemy group name.
- The "context_text" field contains surrounding dialogue lines from the same event page, with speaker names prefixed as [Name].
  Use context_text to infer speaker identity, tone, pronouns, and terminology consistency.
- When context_text shows [SpeakerName] before a dialogue line, that speaker is the one saying the line — match their voice and personality.
- context_text may also contain [Event: name] tags (the RPG Maker event name, useful for scene context) and Note: lines (event notes, sometimes containing hints about the scene).
- Maintain consistent terminology for the same game concepts across all items in the batch (e.g. always use the same word for "skill", "item", "quest").
- Do NOT translate context_text unless it is also the item's source field.

## Common pitfalls
- Do NOT add explanations, notes, or prefixes like "Translation:" to target text.
- Do NOT translate placeholder tokens — \\N[1] stays as \\N[1], not a name.
- If a source string is only tokens/whitespace (e.g. "\\N[1]"), copy it unchanged.
- Preserve full-width punctuation (：。、「」 etc.) only if appropriate for the target language; convert to target-language equivalents.
- Do NOT transliterate names unless the target language convention requires it (e.g. CJK to Vietnamese: keep original CJK or use established readings).
- When translating database names (actors, enemies, items, skills, states): keep them SHORT. UI slots in RPG Maker are typically 12-20 characters wide. A name that is too long will overflow or be truncated.
- Be careful with \\r\\n in skill description strings — these represent line breaks in the game UI. Preserve them.
"""

_LANG_SPECIFIC_RULES: dict[str, str] = {
    "vi": """## Vietnamese-specific rules
- Use natural Vietnamese pronouns appropriate to the character's age/status/relationship:
  • Close friends/same age: tao-mày (very casual), mình-cậu (friendly)
  • Older sibling dynamic: anh-em (romantic or sibling), chị-em (female older)
  • Adult to child: chú-cháu (uncle), cô-cháu (aunt), bác-cháu (elder)
  • Teacher/mentor: thầy-cô to students
  • Formal/respectful: tôi (self) + appropriate title for other (ngài, quý ngài)
  • Infer to player (game tutorial/guide): use "bạn" or omit pronoun
  Match the pronoun pair to the relationship shown in context_text.
- Japanese honorific suffixes → Vietnamese equivalents (adapt to the relationship, not literally):
  • -さん: appropriate title (anh/chị/cậu/bác) or omit
  • -くん: for younger/male peers, use tên không kèm danh xưng or "cậu"
  • -ちゃん: intimate/childish, use tên thân mật or add "nhỏ/bé"
  • -先生: "thầy"/"cô"
  • -様/さま: "ngài"/"quý ngài" (very formal)
- Avoid overly formal or stiff phrasing that sounds machine-translated.
- Keep RPG terms consistent throughout the batch (e.g. always use the same word for "skill"→"kỹ năng", "item"→"vật phẩm", "quest"→"nhiệm vụ").
- Honorifics and address forms should match the character's personality and social role.
- For battle messages with %1/%2: keep the format, e.g. "%1 sử dụng %2".
- CJK full-width punctuation → Vietnamese equivalents: ：→:, 。→., 、→,, 「」→"", 〜→~.
- For speaker names: keep CJK proper nouns as-is or use Vietnamese readings. If a character has a name in both CJK and alphabetic form, prefer the alphabetic form. When mixing CJK names with Vietnamese address forms, keep the name intact and add the Vietnamese address word before it (e.g. "anh 健太" or "Akina-kun" → "Akina").
- For state names/descriptions: keep them concise and use Vietnamese RPG terminology (độc, choáng, chết, v.v.).
- For System.json battle messages: %1 is the battler name, %2 is the skill/name. Use natural Vietnamese: "%1 nhận %2 sát thương!" not word-for-word order from Japanese.
- For \\\\G token (currency unit): keep it exactly as \\\\G — the game substitutes the currency name at runtime.
- For choices: keep them short since they appear in choice windows. 2-4 words is ideal.
- When the source text mixes Japanese control characters with Vietnamese partial translations (common in already-partially-translated games), translate the remaining Japanese text to Vietnamese and keep any already-Vietnamese text consistent.
- For Japanese onomatopoeia/mimetic words common in VN games (ドキドキ, ワー, うぅ…, ふふっ): translate to natural Vietnamese equivalents (thình thịch, ôi, ừm…, hihi) that match the emotion, not literally.
""",
    "ja": """## Japanese-specific rules
- Use appropriate keigo level matching the character's social role and relationship:
  • Polynomial/rough male: だ/だろう/…ぜ/…ぞ/…べ (俺/オレ)
  • Polite-neutral: です/ます (私/わたし)
  • Formal/humble: でございます/いたします (弊存)
  • Female casual: だわ/の/わ/ね (私/あたし)
  • Elderly: じゃ/…よ (ワシ/儂)
  Match the speech style to character personality shown in context_text.
- Preserve sentence-final particles and speech patterns that define character personality (だ/である/だわ/の/わ/ぜ/ぞ/かしら/なの etc.).
- Keep katakana loanwords for modern/foreign concepts; use kanji/kana for traditional RPG terms.
- Japanese onomatopoeia/mimetic words (ドキドキ, ワー, うぅ…, ふふっ): keep them in natural Japanese — do NOT translate to another language.
- CJK full-width punctuation: preserve as-is (：。、「」、〜).
- For item/skill descriptions: keep concise; maintain existing \\i[N] icon token positions.
- For battle messages with %1/%2: keep the format, e.g. "%1は%2を使った！"
- For choices: keep them short since they appear in choice windows.
- For speaker names: keep CJK proper nouns as-is. Do NOT transliterate to romaji.
- For state names: use standard Japanese RPG terminology (戦闘不能, 毒, 眠り, 麻痺, etc.).
""",
    "zh": """## Chinese-specific rules
- Use Simplified Chinese unless the context clearly calls for Traditional (e.g. Taiwan/Hong Kong game).
- Keep RPG terminology consistent throughout the batch: 技能→skill, 物品→item, 任务→quest, 装备→equipment, 魔法→magic.
- Match formality level to the character's role and the scene's tone.
- Preserve CJK punctuation style (：。、「」、～) consistent with Chinese conventions.
- For character dialogue: distinguish register by social role — formal characters use 您/阁下, casual characters use 你/咱.
- Japanese honorific suffixes → Chinese equivalents: -さん→先生/女士 (or omit), -くん→同学/小+surname, -ちゃん→小+name, -先生→老师, -様→大人.
- Chinese onomatopoeia for Japanese mimetic words: ドキドキ→扑通扑通, ワー→哇, うぅ…→呜…, ふふっ→呵呵.
- For battle messages with %1/%2: keep format, e.g. "%1使用了%2！"
- For state names: use standard Chinese RPG terminology (战斗不能, 中毒, 睡眠, 麻痹, etc.).
- For speaker names: Japanese CJK names may have standard Chinese readings — use them when established (e.g. 健太→健太). Pure kana names: transliterate to Chinese.
- For choices: keep them short and concise.
""",
    "ko": """## Korean-specific rules
- Use appropriate speech level matching the character's relationship and personality:
  • Formal/polite (존댓말): 합쇼체(–습니다/–ㅂ니다), 해요체(–어요/–아요)
  • Casual/intimate (반말): 해체(–다/–는다), 친근(–어/–아)
  • Honorific: 시/으시 prefix for respected persons
  Match the speech level to character personality shown in context_text.
- Preserve sentence-final particles that characterize speech style (–요, –다, –까, –군, –네, –지 etc.).
- Keep RPG terms consistent throughout the batch: 스킬→skill, 아이템→item, 퀘스트→quest, 장비→equipment.
- Japanese honorific suffixes → Korean equivalents: -さん→씨/님, -くん→군, -ちゃん→양, -先生→선생님, -様→님.
- Korean onomatopoeia for Japanese mimetic words: ドキドキ→두근두근, ワー→와아, うぅ…→으음…, ふふっ→후후.
- For battle messages with %1/%2: keep format, e.g. "%1이(가) %2을(를) 사용했다!"
- CJK punctuation → Korean: ：→:, 。→., 、→,, 「」→"", 〜→~.
- For speaker names: Japanese CJK names → Korean Hanja readings when available (健太→겐타), otherwise transliterate katakana names via Korean phonology.
- For state names: use standard Korean RPG terminology (전투불능, 독, 수면, 마비, etc.).
- For choices: keep them short and concise.
""",
    "en": """## English-specific rules
- Use natural English phrasing; avoid translation-ese or overly literal constructions.
- Keep RPG terms consistent (skill, item, quest, equipment, armor, weapon, magic, stats, etc.).
- For character dialogue: match tone and formality level of the original — casual speech for friends, archaic/formal for nobles, gruff for soldiers.
- For UI text: use concise, standard gaming terminology (Attack, Defend, Item, Magic, Escape, Save, Load, etc.).
- Japanese honorific suffixes → English handling:
  • -さん: usually omit (or use Mr./Ms. in very formal settings)
  • -くん/-ちゃん: omit or use first name only (intimate), or keep as cultural flavor if setting demands
  • -先生: "Professor" or "Master"
  • -様: "Lord"/"Lady" or "Sir"/"Madam"
- Japanese onomatopoeia → English: ドキドキ→thump-thump/pounding heart, ワー→whoa/wow, うぅ…→ugh…, ふふっ→heh/hehe.
- For battle messages with %1/%2: keep format, e.g. "%1 used %2!" or "%1 takes %2 damage!"
- CJK punctuation → English: ：→:, 。→., 、→,, 「」→"", 〜→~.
- For state names: use standard English RPG terminology (KO/Death, Poison, Sleep, Paralysis, Silence, etc.).
- For speaker names: transliterate CJK names to romaji (Hepburn for Japanese, Pinyin for Chinese). Keep fantasy names as-is.
- For choices: keep them short and punchy. 1-3 words ideal.
- For already-partially-translated games: keep any existing English text consistent.
""",
    "th": """## Thai-specific rules
- Use natural Thai phrasing appropriate to context — formal for UI/system text, casual for dialogue.
- Keep RPG terms consistent throughout the batch: สกิล→skill, ไอเทม→item, เควสต์→quest, อาวุธ→weapon, เกราะ→armor.
- Maintain polite particles (ครับ/ค่ะ) matching character gender/role when inferable from context_text.
- Thai personal pronouns by relationship:
  • Friends/casual: ฉัน/เรา (self), คุณ/เธอ (other)
  • Superior/respect: หนู (self, humble), คุณ/ท่าน (other, respectful)
  • Intimate/romantic: ผม (male self), น้อง/พี่ (other)
  • Royal/very formal: ข้าพระพุทธเจ้า/กระผม
- Japanese honorific suffixes → Thai: -さん→คุณ, -くん→น้อง, -ちゃん→น้อง/หนู, -先生=>อาจารย์/ครู, -様→ท่าน.
- For battle messages with %1/%2: keep format, e.g. "%1 ใช้ %2"
- CJK punctuation → Thai: ：→:, 。→., 、→,, 「」→"", 〜→~
- For speaker names: transliterate Japanese names to Thai phonology when natural (e.g. サクラ→ซากุระ). Keep English/alphanumeric names as-is.
- For state names: use Thai RPG terminology (ตาย/KO, พิษ, หลับ, เกลือก, ใบ้, etc.).
- For choices: keep them short and clear. 2-4 words ideal.
- Japanese onomatopoeia → Thai: ドキドキ→ตึกตัก, ワー→ว้าว, うぅ…→อุ๊…, ふふっ→ฮี่ฮี่.
""",
    "id": """## Indonesian-specific rules
- Use natural Bahasa Indonesia; avoid overly formal or stiff phrasing that sounds machine-translated.
- Keep RPG terms consistent throughout the batch: skill→"keterampilan" or "skill" (gaming context prefers "skill"), item→"barang"/"item", quest→"misi"/"quest", equipment→"perlengkapan", senjata→weapon.
- Match tone and register to the character's personality shown in context_text.
- Indonesian personal pronouns by relationship:
  • Casual/friends: aku-kamu, gue-lo (very informal/slang)
  • Polite/neutral: saya-anda
  • Older/respected: saya-Bapak/Ibu
  • Intimate/romantic: aku-kamu
- Japanese honorific suffixes → Indonesian: -さん→Bapak/Ibu/Kak (by age), -くん→Kak/Adik, -ちゃん→Adik/engkau, -先生→Guru/Prof, -様→Tuan/Nyonya.
- For battle messages with %1/%2: keep format, e.g. "%1 menggunakan %2!"
- CJK punctuation → Indonesian: ：→:, 。→., 、→,, 「」→"", 〜→~.
- For speaker names: transliterate Japanese names to Indonesian phonology naturally (e.g. サクラ→Sakura). Keep established romaji as-is.
- For state names: use Indonesian RPG terminology (KO/Mati, Racun, Tidur, Lumpuh, Bisu, etc.).
- For choices: keep them short and clear. 2-4 words ideal.
- Japanese onomatopoeia → Indonesian: ドキドキ→deg-degan, ワー→wah, うぅ…→uh…, ふふっ→hihi.
""",
    "pt": """## Portuguese-specific rules
- Use natural Brazilian Portuguese unless European Portuguese context is clear (use "você" not "tu" in Brazilian, keep formal register clear).
- Keep RPG terms consistent throughout the batch: habilidade→skill, item→item, missão→quest, equipamento→equipment, espada→sword.
- Match formality level to character role and scene tone — use tu (informal BR/PT) vs você (formal BR) vs o senhor/a senhora (very formal) appropriately.
- Portuguese personal pronouns by relationship:
  • Friends/casual: eu-você/te (BR), eu-tu (PT/informal BR)
  • Formal/respectful: eu-o senhor/a senhora
  • Intimate/romantic: eu-você/meu bem
- Japanese honorific suffixes → Portuguese: -さん→Sr./Sra./Seu/Dona (by context), -くん→omit or "menino", -ちゃん→diminutive, -先生→Mestre/Professor, -様→Senhor/Senhora.
- For battle messages with %1/%2: keep format, e.g. "%1 usou %2!" or "%1 sofreu %2 de dano!"
- CJK punctuation → Portuguese: ：→:, 。→., 、→,, 「」→"", 〜→~.
- For speaker names: transliterate CJK names naturally. Japanese → Hepburn romaji as-is. Chinese → Pinyin.
- For state names: use Portuguese RPG terminology (KO/Morto, Veneno, Sono, Paralisia, Silêncio, etc.).
- For choices: keep them short and clear. 2-4 words ideal.
- Japanese onomatopoeia → Portuguese: ドキドキ→tum-tum/batida, ワー→uau, うぅ…→uh…, ふふっ→hehe.
""",
    "ru": """## Russian-specific rules
- Use natural Russian with appropriate grammatical cases — nouns/adjectives must agree in gender, number, and case.
- Keep RPG terms consistent throughout the batch: навык→skill, предмет→item, задание→quest, снаряжение→equipment, магия→magic.
- Match formality (ты/Вы) to the character relationship and scene — friends use ты, strangers/elders use Вы.
- Russian personal pronouns and address by relationship:
  • Friends/casual: я-ты
  • Formal/respectful: я-Вы
  • Intimate/romantic: я-ты (with diminutives)
  • Superior/teacher: я-Вы/Вам
- Japanese honorific suffixes → Russian: -さん→-сан (keep) or господин/госпожа, -くん→-кун (keep), -ちゃん→-тян (keep), -先生→сэнсэй, -様→-сама/господин.
- For battle messages with %1/%2: keep format, e.g. "%1 использует %2!" — ensure Russian case agreement.
- CJK punctuation → Russian: ：→:, 。→., 、→,, 「」→«»/\"..", 〜→~.
- For speaker names: transliterate Japanese via Polivanov system (タカシ→Такаси), Chinese via Palladius (健太→Цзяньтай).
- For state names: use Russian RPG terminology (Гибель/KO, Яд, Сон, Паралич, Молчание, etc.).
- For choices: keep them short and clear. 2-4 words ideal.
- Japanese onomatopoeia → Russian: ドキドキ→тук-тук, ワー→вау, うぅ…→уу…, ふふっ→хехе.
""",
    "fr": """## French-specific rules
- Use natural French; avoid anglicisms unless standard in gaming context (e.g. "skill"→"compétence" not "skill", but "boss" is acceptable).
- Keep RPG terms consistent throughout the batch: compétence→skill, objet→item, quête→quest, équipement→equipment, arme→weapon.
- Match register (tu/vous) to character relationship — friends/peers use tu, strangers/elders/superiors use vous.
- French personal pronouns and address by relationship:
  • Friends/casual: je-tu
  • Formal/respectful: je-vous
  • Intimate/romantic: je-tu (with terms of endearment)
  • Royal/very formal: je-Vous/votre majesté
- Japanese honorific suffixes → French: -さん→Monsieur/Madame, -くん→omit or prénom, -ちゃん→diminutif, -先生→Maître/Professeur, -様→Seigneur/Dame.
- For battle messages with %1/%2: keep format, e.g. "%1 utilise %2 !" (note: French typography puts space before !).
- CJK punctuation → French: ：→:, 。→., 、→,, 「」→«»/\"..", 〜→~.
- For speaker names: transliterate Japanese names via Hepburn romaji as-is. Chinese → Pinyin.
- For state names: use French RPG terminology (KO/Mort, Poison, Sommeil, Paralysie, Silence, etc.).
- For choices: keep them short and clear. 2-4 words ideal.
- Japanese onomatopoeia → French: ドキドキ→boum-boum, ワー→ouah, うぅ…→ouh…, ふふっ→hihi.
- French typography: spaces before !, ?, :, ;, and inside guillemets « ».
""",
    "de": """## German-specific rules
- Use natural German; follow standard gaming localization conventions.
- Keep RPG terms consistent throughout the batch: Fähigkeit→skill, Gegenstand→item, Auftrag→quest, Ausrüstung→equipment, Waffe→weapon.
- Match register (du/Sie) to character relationship — friends/peers use du, formal/strangers use Sie.
- German personal pronouns and address by relationship:
  • Friends/casual: ich-du
  • Formal/respectful: ich-Sie
  • Intimate/romantic: ich-du (with Nickname/Kosename)
  • Elderly/respected: ich-Sie
- Japanese honorific suffixes → German: -さん→Herr/Frau, -くん→omit or Vorname, -ちゃん→Kosename/Diminutiv, -先生→Meister/Professor, -様→Herr/Frau/Gnädige.
- For battle messages with %1/%2: keep format, e.g. "%1 benutzt %2!" or "%1 erleidet %2 Schaden!"
- CJK punctuation → German: ：→:, 。→., 、→,, 「」→«»/\"..", 〜→~.
- For speaker names: transliterate Japanese names via Hepburn romaji. Chinese → Pinyin.
- For state names: use German RPG terminology (KO/Tod, Gift, Schlaf, Lähmung, Stille, etc.).
- For choices: keep them short and clear. 2-4 words ideal.
- Japanese onomatopoeia → German: ドキドキ→bumm-bumm, ワー→wow, うぅ…→uh…, ふふっ→hihi.
- German noun capitalization: capitalize all nouns as per German orthography, even in UI labels.
""",
    "es": """## Spanish-specific rules
- Use natural Spanish; standard Latin American Spanish unless context clearly requires European (vosotros in Spain, ustedes in LatAm).
- Keep RPG terms consistent throughout the batch: habilidad→skill, objeto→item, misión→quest, equipo→equipment, arma→weapon.
- Match register (tú/usted) to character relationship — friends/peers use tú, formal/elders use usted.
- Spanish personal pronouns and address by relationship:
  • Friends/casual: yo-tú
  • Formal/respectful: yo-usted
  • Intimate/romantic: yo-tú (with cariño/cielo)
  • Very formal: yo-su merced/su señoría
- Japanese honorific suffixes → Spanish: -さん→Señor/Señora, -くん→chico/omit, -ちゃん→diminutivo, -先生→Maestro/Profesor, -様→Señor/Señora/Dama.
- For battle messages with %1/%2: keep format, e.g. "¡%1 usó %2!" or "¡%1 recibe %2 de daño!"
- CJK punctuation → Spanish: ：→:, 。→., 、→,, 「」→«»/\"..", 〜→~.
- For speaker names: transliterate Japanese names via Hepburn romaji. Chinese → Pinyin.
- For state names: use Spanish RPG terminology (KO/Muerto, Veneno, Sueño, Parálisis, Silencio, etc.).
- For choices: keep them short and clear. 2-4 words ideal.
- Japanese onomatopoeia → Spanish: ドキドキ→tum-tum, ワー→¡guau!, うぅ…→uh…, ふふっ→jeje.
- Spanish punctuation: inverted ¿? and ¡! for questions and exclamations when appropriate in dialogue.
""",
    "it": """## Italian-specific rules
- Use natural Italian; avoid overly literal translations from English or Japanese.
- Keep RPG terms consistent throughout the batch: abilità→skill, oggetto→item, missione→quest, equipaggiamento→equipment, arma→weapon.
- Match register (tu/Lei) to character relationship — friends/peers use tu, formal/strangers use Lei.
- Italian personal pronouns and address by relationship:
  • Friends/casual: io-tu
  • Formal/respectful: io-Lei
  • Intimate/romantic: io-te/tu (with terms of endearment)
  • Elderly/respected: io-Lei/Voi (southern Italy)
- Japanese honorific suffixes → Italian: -さん→Signor/Signora, -くん→omit or nome, -ちゃん→vezzeggiativo, -先生→Maestro/Professore, -様→Signore/Signora/Dama.
- For battle messages with %1/%2: keep format, e.g. "%1 usa %2!" or "%1 subisce %2 danni!"
- CJK punctuation → Italian: ：→:, 。→., 、→,, 「」→«»/\"..", 〜→~.
- For speaker names: transliterate Japanese names via Hepburn romaji. Chinese → Pinyin.
- For state names: use Italian RPG terminology (KO/Morto, Veleno, Sonno, Paralisi, Silenzio, etc.).
- For choices: keep them short and clear. 2-4 words ideal.
- Japanese onomatopoeia → Italian: ドキドキ→tic-tac/batticuore, ワー→wow, うぅ…→uh…, ふふっ→hihi.
""",
    "pl": """## Polish-specific rules
- Use natural Polish with appropriate grammatical cases — nouns/adjectives must agree in gender, number, and case.
- Keep RPG terms consistent throughout the batch: umiejętność→skill, przedmiot→item, zadanie→quest, ekwipunek→equipment, bro→weapon.
- Match formality (ty/Pan/Pani) to character relationship — friends use ty, formal/strangers use Pan/Pani.
- Polish personal pronouns and address by relationship:
  • Friends/casual: ja-ty
  • Formal/respectful: ja-Pan/Pani
  • Intimate/romantic: ja-ty (with zdrabniania/diminutives)
  • Superior/teacher: ja-Pan/Pani
- Japanese honorific suffixes → Polish: -さん→Pan/Pani, -くん→pominięte lub imię, -ちゃん→zdrobnienie, -先生→Mistrz/Profesor, -様→Pan/Pani/Wielmożny.
- For battle messages with %1/%2: keep format, e.g. "%1 używa %2!" — ensure Polish case agreement.
- CJK punctuation → Polish: ：→:, 。→., 、→,, 「」→«»/\"..", 〜→~.
- For speaker names: transliterate Japanese names via Hepburn romaji. Chinese → Pinyin.
- For state names: use Polish RPG terminology (Śmierć/KO, Trucizna, Sen, Paraliż, Milczenie, etc.).
- For choices: keep them short and clear. 2-4 words ideal.
- Japanese onomatopoeia → Polish: ドキドキ→puk-puk, ワー→łau/wow, うぅ…→uu…, ふふっ→hihi.
""",
    "tr": """## Turkish-specific rules
- Use natural Turkish; avoid overly literal or stiff constructions.
- Keep RPG terms consistent throughout the batch: yetenek→skill, eşya→item, görev→quest, ekipman→equipment, silah→weapon.
- Match formality (sen/siz) to character relationship — friends use sen, formal/elders use siz.
- Turkish personal pronouns and address by relationship:
  • Friends/casual: ben-sen
  • Formal/respectful: ben-siz
  • Intimate/romantic: ben-sen (with sevgili/canım)
  • Very formal: ben-siz/Siz
- Japanese honorific suffixes → Turkish: -さん→Bey/Hanım, -くん→omit veya ad, -ちゃん→küçük + ad, -先生→Usta/Hoca, -様→Bey/Hanım/Efendi.
- For battle messages with %1/%2: keep format, e.g. "%1 %2 kullandı!" — Turkish SOV word order.
- CJK punctuation → Turkish: ：→:, 。→., 、→,, 「」→""/«»", 〜→~.
- For speaker names: transliterate Japanese names naturally (サクラ→Sakura). Keep romaji as-is.
- For state names: use Turkish RPG terminology (KO/Ölü, Zehir, Uyku, Felç, Sessizlik, etc.).
- For choices: keep them short and clear. 2-4 words ideal.
- Japanese onomatopoeia → Turkish: ドキドキ→güm-güm, ワー→vay, うぅ…→ıı…, ふふっ→hihi.
""",
    "ar": """## Arabic-specific rules
- Use natural Modern Standard Arabic (فصحى) unless context clearly calls for a dialect.
- Keep RPG terms consistent throughout the batch: مهارة→skill, عنصر→item, مهمة→quest, معدات→equipment, سلاح→weapon.
- Arabic is RIGHT-TO-LEFT — ensure control codes and placeholders remain in LTR context. Place RTL marks if needed.
- Match formality (أنتَ/أنتِ/حضرة) to character relationship and gender — أنتَ (male), أنتِ (female), حضرة (formal).
- Arabic personal pronouns and address by relationship:
  • Friends/casual: أنا-أنتَ/أنتِ
  • Formal/respectful: أنا-سيدي/سيدتي
  • Intimate/romantic: أنا-أنتَ/أنتِ (with حبيبي/حبيبتي)
  • Very formal: أنا-حضرتك/سعادتك
- Japanese honorific suffixes → Arabic: -さん→السيد/السيدة, -くん→فتى/محذوف, -ちゃん→تصغير, -先生→أستاذ/معلم, -様→سيدي/سيدتي.
- For battle messages with %1/%2: keep format, e.g. "استخدم %1 %2!" — ensure RTL/LTR mixing is handled correctly.
- CJK punctuation → Arabic: ：→:, 。→., 、→,, 「」→""/«»", 〜→~.
- For speaker names: transliterate Japanese names to Arabic script (サクラ→ساكورا). Chinese → Pinyin in Arabic script.
- For state names: use Arabic RPG terminology (وفاة/KO, سم, نوم, شلل, صمت, etc.).
- For choices: keep them short and clear. 2-4 words ideal. Place after RTL marker if needed.
- Japanese onomatopoeia → Arabic: ドキドキ→دق-دق, ワー→واو, うぅ…→أو…, ふふっ→هيهي.
- CRITICAL: When mixing RTL Arabic with LTR control codes (\\N[1], \\C[2], etc.), the control codes MUST remain in their original LTR form. Do NOT reverse, mirror, or reorder them.
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
        system_prompt = _build_system_prompt(target_lang, self.glossary_block)
        max_tokens = min(_estimate_output_tokens(masked_entries), 8192)
        user_prompt = _user_prompt(masked_entries, target_lang, source_lang)
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
