# Game LLM Translator

[Tiếng Việt](README.vi.md)

Translate RPG Maker MV/MZ and Unity (XUnity AutoTranslator) game text with LLMs or MTL providers. Includes a desktop GUI with progress tracking, dark mode, glossary support, parallel workers, and automatic retry.

## Key features

- **RPG Maker MV/MZ**: extract `www/data/*.json` or `data/*.json`, translate, and apply while preserving nested folder structure including custom plugin subdirectories (PKD_PhoneMenu, etc.)
- **Unity XUnity AutoTranslator**: extract/apply `Translation/{Lang}/Text/*.txt` files (`original=translation` format); preserve regex rules/scoping directives in processed files, skip resizer files
- **Shared translate pipeline** (`translate_pipeline.py`): GUI, CLI, and `auto` use the same retry, memory, dedup, and checkpoint logic
- **Parallel translation**: 1-8 workers on GUI, `translate`, `pipeline`, and `auto` (`--workers`)
- **Source pre-deduplication**: groups by `(source, context_category)`, calls the LLM once, fans out to siblings (reduces API calls)
- **Smart retry**: reads `retry_after` from Cloudflare 524, defers failed batches, retries with backoff, falls back to source if still failing
- **Placeholder checks**: warns when RPG control codes (`\\V[1]`, `%1`, etc.) are missing from translations
- **Translation memory**: per-game and global memory, file-locked to avoid corruption during parallel writes
- **Atomic CSV writes**: temporary file + `os.replace`, safe against crashes during save
- **Glossary CSV**: inject fixed terms into the LLM system prompt (GUI or `--glossary` on CLI); `validate --glossary` lints for duplicates, blank translations, and case conflicts
- **Correction table CSV**: post-translation find/replace rules applied after each batch (GUI or `--correction-table` on CLI)
- **Namebox preservation & persistence**: auto-restores YEP_MessageCore `\n<Name>` prefixes; speaker names are translated once in a pre-pass and stored in `translations.namebox.csv` so resume runs don't re-spend tokens
- **Token formatting fix**: auto-repairs LLM-introduced spaces in RPG Maker tokens (`\N [1]` → `\N[1]`, `% 1` → `%1`)
- **Dynamic batching by character length**: limits batch size by total characters (`max_chars`) to prevent token overflow on long entries
- **Multi-provider**: Anthropic Claude, OpenAI/OpenAI-compatible (OpenRouter, LM Studio, Ollama), Google MTL, MyMemory, LibreTranslate, Microsoft, Yandex
- **15-language prompt system**: detailed language-specific rules for Vietnamese, Japanese, Chinese, Korean, English, Thai, Indonesian, Portuguese, Russian, French, German, Spanish, Italian, Polish, Turkish, and Arabic — with pronoun maps, honorific handling, onomatopoeia, punctuation conversion, and name transliteration
- **Cheat plugin**: install/uninstall RPG Maker MV/MZ Cheat UI Plugin (prefers optional bundled archive/cache, falls back to GitHub release)
- **Desktop GUI** (PySide6 / Qt6):
  - Light/dark mode toggle (custom Qt palette)
  - QTabWidget — instant tab switching with no flicker
  - Progress bar with translated/total count
  - Editor with filters (All / Untranslated+Fallback / Translated), search, and red fallback-row highlights
  - Backups tab: create/restore/delete multiple backups; clear game/global memory; clear old translations
  - Stop button cancels between batches/retry delays and prevents further writes after stop

## Installation

```bash
git clone https://github.com/lamhaison123/game-llm-translator
cd game-llm-translator
python -m venv .venv
# Windows PowerShell: .venv\Scripts\Activate.ps1
# Windows cmd:        .venv\Scripts\activate.bat
# Linux/macOS:        source .venv/bin/activate
pip install -e .
cp .env.example .env       # optional
```

`.env` (optional — you can also enter these in the GUI):

```env
ANTHROPIC_API_KEY=sk-ant-...
OPENAI_API_KEY=
LLM_PROVIDER=anthropic
LLM_MODEL=claude-opus-4-7
```

## GUI (recommended)

```bash
game-translator-gui
```

Or download the prebuilt executable from [Releases](https://github.com/lamhaison123/game-llm-translator/releases) (Windows and Linux builds available).

GUI workflow:
1. **Game** tab: choose game folder → Scan
2. **Provider** tab: choose provider, model, API key/base URL
3. **Translate** tab: Auto-translate or Extract+Translate+Export; tune batch size, workers, glossary, memory
4. **Review** tab: edit `translations.csv` with filter/search
5. **Apply** tab: export to a new folder or apply directly to the game (automatic backup; RPG Maker JSON and Unity XUnity TXT)
6. **Backups** tab: manage backups and clear memory

## Automatic CLI translation

```bash
game-translator auto "D:/Games/MyRPG" --provider anthropic --target Vietnamese
```

Detect engine, extract text, translate, and create `translator_work/translated_data` while preserving folder structure.

Common options:

```bash
game-translator auto "D:/Games/MyRPG" \
  --provider openai-compatible \
  --model deepseek-r1 \
  --api-base https://your-proxy/v1 \
  --target Vietnamese \
  --in-place           # write directly into the game after auto backup
  --restart            # ignore existing translations and translate from scratch
  --glossary terms.csv \
  --correction-table corrections.csv \
  --workers 4
```

Scan before translating to see how much text will be processed:

```bash
game-translator scan "D:/Games/MyRPG"
```

## Detailed CLI workflow

```bash
# 1. Extract RPG Maker MV/MZ
game-translator extract rpg-maker "D:/Games/MyRPG" -o work/texts.csv

# 2. Translate
game-translator translate work/texts.csv -o work/translations.csv --target Vietnamese

# 3. Apply RPG Maker
game-translator apply rpg-maker work/translations.csv --out-dir work/translated_data

# Or run the full RPG Maker pipeline
game-translator pipeline rpg-maker "D:/Games/MyRPG" --work-dir work --target Vietnamese
```

Post-translation utilities:

```bash
# Lint a translations CSV (placeholder mismatches, fallback %, noun inconsistencies,
# untranslated namebox names). Optionally lint a glossary file too.
game-translator validate work/translations.csv --glossary terms.csv --strict

# Re-translate a subset of rows in place (fallback rows, empty targets, or a
# specific context). Useful after tweaking the glossary or correction table.
game-translator retry work/translations.csv --filter fallback --target Vietnamese

# Carry forward translations to a re-extracted texts.csv (e.g. after a game
# patch). Produces a translations-shaped CSV with new/changed/removed audited.
game-translator diff work/texts_old.csv work/texts_new.csv work/translations.csv \
  -o work/translations_carry.csv
```

For Unity XUnity, use `game-translator auto` or the GUI to extract/apply `Translation/{Lang}/Text/*.txt` directly. The legacy `extract unity` command only scans CSV/TSV/JSON/TXT candidates for manual CSV export.

## Glossary

Create a CSV file named `glossary.csv`:

```csv
term,translation,note
勇者,Dũng giả,protagonist
ポーション,Bình thuốc,common item
HP,HP,keep as-is
```

In the GUI: Translate tab → Advanced → Glossary CSV → Browse.
CLI: `--glossary terms.csv`

Each batch injects the glossary into the system prompt so the LLM must follow the fixed term translations.

## Correction table

Create a CSV with `find` and `replace` columns to fix consistent terminology after translation:

```csv
find,replace
MP,Ma lực
HP,Sinh lực
Skill,Kỹ năng
```

In the GUI: Translate tab → Advanced → Correction table CSV → Browse.
CLI: `--correction-table corrections.csv`

Applied as simple string replacements after every batch (including retries).

## Provider examples

```bash
# Anthropic
game-translator translate work/texts.csv --provider anthropic --model claude-opus-4-7

# OpenAI
game-translator translate work/texts.csv --provider openai --model gpt-4o

# OpenAI-compatible (OpenRouter, LM Studio, Ollama, custom proxy)
game-translator translate work/texts.csv \
  --provider openai-compatible \
  --model deepseek-r1 \
  --api-base https://openrouter.ai/api/v1

# MTL (free, no API key required)
game-translator translate work/texts.csv --provider google
game-translator translate work/texts.csv --provider mymemory
```

`bing` requires `MICROSOFT_TRANSLATOR_KEY`; `yandex` requires `YANDEX_TRANSLATE_API_KEY` + `YANDEX_FOLDER_ID`; `libretranslate` can point to a self-hosted server via `LIBRETRANSLATE_URL`.

## Cheat plugin (RPG Maker MV/MZ)

In the GUI Apply tab → Cheat plugin → Apply Cheat. The tool prefers a bundled archive if the build includes `vendor/cheat/`, then local cache, then GitHub release. When installing, it copies the script to `www/js/plugins/`, registers it in `plugins.js`, and saves a manifest so uninstall can clean up later.

In-game toggle: **Ctrl+C**.

## Unity (XUnity AutoTranslator)

The tool translates `Translation/{Lang}/Text/*.txt` files generated by XUAT. Workflow:

1. **Install XUnity AutoTranslator** into the game (via BepInEx or MelonLoader). See [upstream docs](https://github.com/bbepis/XUnity.AutoTranslator).
2. **Run the game once** so XUAT collects text and creates `Translation/{Lang}/Text/_AutoGeneratedTranslations.txt`.
3. **Open the GUI** → choose game folder → Scan. The tool auto-detects the `unity-xunity` engine.
4. **Auto-translate** or **Extract+Translate+Export Copy**.
5. Output is written to `translator_work/translated_data/{Lang}/Text/*.txt`. Copy it into the game's `Translation/{Lang}/Text/`, or use "Apply to game" in the GUI to backup and copy TXT files into `Translation/`.
6. **Reload in game**: press `Alt+R` so XUAT reloads translations.

CLI:
```bash
game-translator auto "C:/Games/UnityGame" --target Vietnamese
```

File format:
- `original=translation` (one entry per line)
- The tool **preserves** `#directives`, `r:"regex"=replacement`, and `sr:"splitter"=...` in processed files; `*resizer.txt` is skipped
- Only normal `original=translation` lines are translated

## CSV format

`texts.csv`:

```csv
file,key,source,context,context_text
/path/Actors.json,$[1].name,Harold,rpg_maker_json,
/path/Map001.json,$[1].events[1].pages[0].list[0].parameters[0],Hello!,rpg_maker_event_text,
```

`translations.csv`:

```csv
file,key,source,target,context
/path/Actors.json,$[1].name,Harold,Ha-rôn,rpg_maker_json
```

`(file, key)` is the unique identity. Entries with the same `key` in different files are not mixed up.

## Resume & checkpoints

Translation saves after each batch, so you can stop and restart at any time. On the next run:
- entries that already have a target are skipped
- entries with the same `source` in memory are reused immediately without calling the LLM
- use `--restart` to ignore everything and translate from scratch

## Safety

- GUI automatically backs up the game before apply (`data_backup_<timestamp>`)
- Restore creates `data_before_restore_<timestamp>` before overwriting
- The tool writes to its own folder (`translator_work/translated_data`); in-place apply only happens when explicitly requested
- Atomic CSV writes + filelock protect memory from parallel/crash corruption
- Do not commit `.env` or API keys

## Tests

```bash
pip install -e ".[dev]"
pytest
```

216 tests cover RPG Maker + Unity extract/apply, cheat plugin, atomic writes, concurrent memory save, glossary, correction table, token formatting, dynamic batching, translation pipeline, and fan-out dedup. 381 tests total including PKD_PhoneMenu extraction, MZ control codes, thread-safe glossary, prompt context hints, namebox race dedup + per-batch persist, prompt cache split, partial-LLM truncation, glossary linting, and diff/retry/validate commands.

## Build executable

### Windows (.exe)

```powershell
pip install -e . pyinstaller
pyinstaller --noconfirm --windowed --name game-translator-gui `
  --paths src --distpath dist --workpath build `
  --add-data "vendor;vendor" --add-data "assets;assets" `
  --icon assets/icon.ico `
  --exclude-module PySide6.QtWebEngineCore `
  --exclude-module PySide6.QtWebEngineWidgets `
  --exclude-module PySide6.QtMultimedia `
  --exclude-module PySide6.Qt3DCore `
  --exclude-module PySide6.QtCharts `
  --exclude-module PySide6.QtDataVisualization `
  --exclude-module PySide6.QtQml `
  --exclude-module PySide6.QtQuick `
  --exclude-module PySide6.QtPdf `
  gui_launcher.py
```

Output: `dist/game-translator-gui/game-translator-gui.exe` (~140MB one-folder mode).

### Linux

Requires Python 3.10+ and Qt6 runtime libraries:

```bash
# Ubuntu/Debian
sudo apt install python3-venv \
  libxkbcommon-x11-0 libxcb-icccm4 libxcb-image0 libxcb-keysyms1 \
  libxcb-randr0 libxcb-render-util0 libxcb-shape0 libxcb-xinerama0 \
  libxcb-xkb1 libxcb-cursor0 libegl1 libglib2.0-0 libdbus-1-3 libgl1
```

Build:

```bash
pip install -e . pyinstaller
pyinstaller --noconfirm --name game-translator-gui \
  --paths src --distpath dist --workpath build \
  --add-data "vendor:vendor" --add-data "assets:assets" \
  --exclude-module PySide6.QtWebEngineCore \
  --exclude-module PySide6.QtWebEngineWidgets \
  --exclude-module PySide6.QtMultimedia \
  --exclude-module PySide6.Qt3DCore \
  --exclude-module PySide6.QtCharts \
  --exclude-module PySide6.QtDataVisualization \
  --exclude-module PySide6.QtQml \
  --exclude-module PySide6.QtQuick \
  --exclude-module PySide6.QtPdf \
  gui_launcher.py
```

Output: `dist/game-translator-gui/game-translator-gui` (binary). Run:

```bash
chmod +x dist/game-translator-gui/game-translator-gui
./dist/game-translator-gui/game-translator-gui
```

**Note**: Linux builds must be produced on Linux (no cross-build from Windows). Use GitHub Actions or Docker to build both platforms in CI.

### macOS

```bash
pip install -e . pyinstaller
pyinstaller --noconfirm --windowed --name game-translator-gui \
  --paths src --distpath dist --workpath build \
  --add-data "vendor:vendor" --add-data "assets:assets" \
  gui_launcher.py
```

Output: `dist/game-translator-gui.app`.

## Architecture

- `cli.py`: Typer CLI (auto, scan, extract, translate, apply, pipeline, edit, diff, retry, validate)
- `gui.py`: PySide6 (Qt6) GUI — QMainWindow + QTabWidget + custom dark/light palette
- `auto.py`: automatic workflow for RPG Maker + Unity
- `rpg_maker_common.py` / `rpg_maker_mv.py` / `rpg_maker_mz.py`: MV/MZ extract + apply
- `rpg_maker_cheat.py`: install/uninstall Cheat UI Plugin
- `xunity.py`: extract + apply Unity XUnity AutoTranslator format
- `translate_pipeline.py`: shared retry/dedup/memory/glossary loop used by CLI, `auto`, and GUI; namebox pre-pass + per-batch persistence
- `llm.py`: providers (Anthropic, OpenAI, MTL); glossary injection; namebox name translation
- `prompts.py`: pure-data language rules (no SDK imports) — system prompt builder split into stable + glossary blocks for Anthropic prompt caching
- `errors.py`: `StoppedByUser` exception used to cancel mid-pipeline
- `diff_tool.py`: carry-forward diff between two extracted CSVs against an existing translations.csv
- `csv_store.py`: atomic CSV save/load
- `translation_memory.py`: filelock + atomic memory store
- `glossary.py`: CSV glossary loader, prompt formatter, and `validate_glossary` linter
- `editor.py`: cross-platform file editor
- `unity.py`: extract Unity CSV/JSON candidates (legacy, CLI only — use `xunity.py` instead)

## Engine support

| Engine | Detect | Extract | Apply | Note |
|---|---|---|---|---|
| RPG Maker MV | Yes | Yes | Yes | full support |
| RPG Maker MZ | Yes | Yes | Yes | full support + plugin command 357 + custom subdirectory extraction (PKD_PhoneMenu, etc.) |
| RPG Maker VX Ace | Yes | Yes | Yes | full support (`.rvdata2` Ruby Marshal codec, vendored from [RPGMTL](https://github.com/MizaGBF/RPGMTL) MIT) |
| Unity (XUnity AutoTranslator) | Yes | Yes | Yes | via `Translation/{lang}/Text/*.txt` |
| RPG Maker VX / XP | Yes | No | No | detect only |

## License

MIT.
