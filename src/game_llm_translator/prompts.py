"""Prompt strings and language-rule data for the LLM translation pipeline.

This module holds the large static text used as the system prompt (RPG Maker
control-code rules, glossary conventions, language-specific guidance for 14
target languages) plus the helpers that assemble those parts into the final
prompt blocks sent to providers.

It is intentionally data-heavy and dependency-light: importing this module must
not require any LLM SDK. `llm.py` re-exports everything here for backward
compatibility with existing imports across the codebase and tests.
"""
from __future__ import annotations


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
- You MUST translate or transliterate the speaker name inside <...> for Latin-script target languages; do not leave CJK speaker names unchanged.
- Keep control codes inside the namebox unchanged, but translate the visible name text after those codes.
- When the name inside <...> contains CJK characters, ALWAYS translate them. For example: \\n<長老> must become \\n<Trưởng lão> (Vietnamese), \\n<长老> must become \\n<Zhanglao> (English), \\n<フォル> must become \\n<Foru>. NEVER leave CJK characters inside <...> untranslated.
- If the same speaker name appears in the dialogue body, use the exact same translated spelling in both the namebox and the body.
- Example for Vietnamese: \\F[greima_10]\\n<\\C[27]グレーマ>「グレーマは……」 -> \\F[greima_10]\\n<\\C[27]Gurema>「Gurema là……」.
- Example for Vietnamese: \\n<\\C[0]長老>「安心したまえ」 -> \\n<\\C[0]Trưởng lão>「Hãy yên tâm」 (NOT \\n<\\C[0]長老>).
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
17. Corner brackets 【…】 (lenticular brackets) in Japanese text are emphasis/title markers — they wrap chapter names, scene titles, skill names, or important terms. Always translate the text INSIDE 【…】 into the target language. The brackets themselves should be converted or preserved according to the target language's punctuation conventions described in the language-specific rules below (e.g. keep 【】 for CJK targets, convert to [] for Latin-script targets). The content inside 【…】 is regular game text (titles, labels, terms); if the content IS a proper noun or established character name, apply the proper-noun rules (transliterate/preserve) rather than translating it.

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
- CJK full-width punctuation → Vietnamese equivalents: ：→:, 。→., 、→,, 「」→"", 〜→~, 【】→[] (corner brackets become square brackets).
- Corner brackets 【…】 wrap chapter/scene titles, skill names, or emphasis — translate the text inside and convert 【】 to [] in the target. Example: 【踊り子ーその２】 → [Vũ công — Phần 2].
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
- CJK full-width punctuation: preserve as-is (：。、「」、〜, 【】). Corner brackets 【】 are native Japanese punctuation — keep them when the target is Japanese.
- Corner brackets 【…】 wrap chapter/scene titles, skill names, or emphasis — translate the text inside and keep 【】 as-is for Japanese targets.
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
- Preserve CJK punctuation style (：。、「」、～) consistent with Chinese conventions. Keep 【】 as-is — they are native Chinese punctuation.
- Corner brackets 【…】 wrap chapter/scene titles, skill names, or emphasis. For Chinese (a CJK target), translate the text inside and keep the 【】 brackets unchanged.
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
- CJK punctuation → Korean: ：→:, 。→., 、→,, 「」→"", 〜→~, 【】→[] (corner brackets become square brackets).
- Corner brackets 【…】 wrap chapter/scene titles, skill names, or emphasis — translate the text inside and convert 【】 to [].
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
- CJK punctuation → English: ：→:, 。→., 、→,, 「」→"", 〜→~, 【】→[] (corner brackets become square brackets).
- Corner brackets 【…】 wrap chapter/scene titles, skill names, or emphasis — translate the text inside and convert 【】 to [].
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
- CJK punctuation → Thai: ：→:, 。→., 、→,, 「」→"", 〜→~, 【】→[] (corner brackets become square brackets).
- Corner brackets 【…】 wrap chapter/scene titles, skill names, or emphasis — translate the text inside and convert 【】 to [].
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
- CJK punctuation → Indonesian: ：→:, 。→., 、→,, 「」→"", 〜→~, 【】→[] (corner brackets become square brackets).
- Corner brackets 【…】 wrap chapter/scene titles, skill names, or emphasis — translate the text inside and convert 【】 to [].
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
- CJK punctuation → Portuguese: ：→:, 。→., 、→,, 「」→"", 〜→~, 【】→[] (corner brackets become square brackets).
- Corner brackets 【…】 wrap chapter/scene titles, skill names, or emphasis — translate the text inside and convert 【】 to [].
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
- CJK punctuation → Russian: ：→:, 。→., 、→,, 「」→«»/\"..", 〜→~, 【】→[] (corner brackets become square brackets).
- Corner brackets 【…】 wrap chapter/scene titles, skill names, or emphasis — translate the text inside and convert 【】 to [].
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
- CJK punctuation → French: ：→:, 。→., 、→,, 「」→«»/\"..", 〜→~, 【】→[] (corner brackets become square brackets).
- Corner brackets 【…】 wrap chapter/scene titles, skill names, or emphasis — translate the text inside and convert 【】 to [].
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
- CJK punctuation → German: ：→:, 。→., 、→,, 「」→«»/\"..", 〜→~, 【】→[] (corner brackets become square brackets).
- Corner brackets 【…】 wrap chapter/scene titles, skill names, or emphasis — translate the text inside and convert 【】 to [].
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
- CJK punctuation → Spanish: ：→:, 。→., 、→,, 「」→«»/\"..", 〜→~, 【】→[] (corner brackets become square brackets).
- Corner brackets 【…】 wrap chapter/scene titles, skill names, or emphasis — translate the text inside and convert 【】 to [].
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
- CJK punctuation → Italian: ：→:, 。→., 、→,, 「」→«»/\"..", 〜→~, 【】→[] (corner brackets become square brackets).
- Corner brackets 【…】 wrap chapter/scene titles, skill names, or emphasis — translate the text inside and convert 【】 to [].
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
- CJK punctuation → Polish: ：→:, 。→., 、→,, 「」→«»/\"..", 〜→~, 【】→[] (corner brackets become square brackets).
- Corner brackets 【…】 wrap chapter/scene titles, skill names, or emphasis — translate the text inside and convert 【】 to [].
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
- CJK punctuation → Turkish: ：→:, 。→., 、→,, 「」→"" or «», 〜→~, 【】→[] (corner brackets become square brackets).
- Corner brackets 【…】 wrap chapter/scene titles, skill names, or emphasis — translate the text inside and convert 【】 to [].
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
- CJK punctuation → Arabic: ：→:, 。→., 、→,, 「」→"" or «», 〜→~, 【】→[] (corner brackets become square brackets).
- Corner brackets 【…】 wrap chapter/scene titles, skill names, or emphasis — translate the text inside and convert 【】 to [].
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


def _build_system_blocks(target_lang: str | None, glossary_block: str = "") -> tuple[str, str]:
    """Return (stable_part, glossary_part). The stable part is identical across
    every batch with the same target language, so providers can cache it. The
    glossary part changes per batch and must NOT share a cache key with stable."""
    lang_key = _lang_code(target_lang, "auto").lower()
    addon = _LANG_SPECIFIC_RULES.get(lang_key, "")
    return SYSTEM_PROMPT_BASE + addon, glossary_block or ""


SYSTEM_PROMPT = SYSTEM_PROMPT_BASE  # kept for backward compat with tests


_NAME_TRANSLATE_SYSTEM = """You translate speaker names from a game. Translate each name to {target_lang} naturally.
- For kanji/hanzi names: use the established reading for the target language.
- For katakana names: transliterate to the target language script (e.g. ディオン → Dion for English, ディオン → Dion for Vietnamese).
- For names already in the target script: keep unchanged.
Return ONLY a JSON object mapping each original name to its translation. No explanation, no markdown fences."""
