# Game LLM Translator

[English](README.md)

Tool dịch text game RPG Maker MV/MZ và Unity (XUnity AutoTranslator) bằng LLM hoặc MTL, có GUI desktop với progress bar, dark mode, glossary, parallel workers và retry tự động.

## Tính năng chính

- **RPG Maker MV/MZ**: extract `www/data/*.json` (hoặc `data/*.json`), dịch, apply giữ nguyên cấu trúc nested folders
- **Unity XUnity AutoTranslator**: extract/apply `Translation/{Lang}/Text/*.txt` (format `original=translation`); preserve regex rules/scoping directives trong file xử lý, skip resizer files
- **Pipeline dịch chung** (`translate_pipeline.py`): GUI, CLI và `auto` dùng chung retry, memory, dedup, checkpoint
- **Parallel translate**: 1-8 workers trên GUI, `translate`, `pipeline`, `auto` (`--workers`)
- **Pre-dedup**: gom theo `(source, context_category)`, gọi LLM 1 lần rồi fan-out (giảm API calls)
- **Retry thông minh**: đọc `retry_after` từ Cloudflare 524, defer batch, retry backoff, fallback source nếu vẫn fail
- **Kiểm tra placeholder**: cảnh báo khi thiếu mã RPG (`\\V[1]`, `%1`, …) trong bản dịch
- **Translation memory**: per-game + global, file-locked để tránh corrupt khi parallel write
- **Atomic CSV writes**: tmp file + `os.replace`, an toàn khi crash giữa lúc save
- **Glossary CSV**: term cố định trong system prompt (GUI hoặc `--glossary` trên CLI); `validate --glossary` lint tìm trùng term, translation trống, conflict hoa-thường
- **Namebox preservation + persist**: tự khôi phục prefix `\n<Name>` của YEP_MessageCore; tên speaker được dịch 1 lần ở pre-pass và lưu sang `translations.namebox.csv` để resume không tốn token dịch lại
- **Multi-provider**: Anthropic Claude, OpenAI/OpenAI-compatible (OpenRouter, LM Studio, Ollama), Google MTL, MyMemory, LibreTranslate, Microsoft, Yandex
- **Cheat plugin**: cài/gỡ RPG Maker MV/MZ Cheat UI Plugin (ưu tiên cache/optional bundled archive, fallback GitHub release)
- **GUI desktop** (PySide6 / Qt6):
  - Light/Dark mode toggle (custom Qt palette)
  - QTabWidget — instant tab switch, không flicker
  - Progress bar với translated/total count
  - Editor với filter (All / Untranslated+Fallback / Translated) + search, fallback rows highlight đỏ
  - Backup tab: tạo/restore/xóa multi-select; clear game/global memory; clear old translation
  - Stop button cancel giữa batch/retry delay, không ghi tiếp khi user dừng

## Cài đặt

```bash
git clone https://github.com/lamhaison123/game-llm-translator
cd game-llm-translator
python -m venv .venv
# Windows PowerShell: .venv\Scripts\Activate.ps1
# Windows cmd:        .venv\Scripts\activate.bat
# Linux/macOS:        source .venv/bin/activate
pip install -e .
cp .env.example .env       # tuỳ chọn
```

`.env` (tuỳ chọn — cũng có thể nhập trong GUI):

```env
ANTHROPIC_API_KEY=sk-ant-...
OPENAI_API_KEY=
LLM_PROVIDER=anthropic
LLM_MODEL=claude-opus-4-7
```

## GUI (khuyến nghị)

```bash
game-translator-gui
```

Hoặc tải Windows exe có sẵn từ [Releases](https://github.com/lamhaison123/game-llm-translator/releases).

Workflow GUI:
1. Tab **Game**: chọn thư mục game → Scan
2. Tab **Provider**: chọn provider, model, API key/base URL
3. Tab **Translate**: Auto-translate hoặc Extract+Translate+Export; tinh chỉnh batch size, workers, glossary, memory
4. Tab **Review**: edit translations.csv với filter/search
5. Tab **Apply**: export sang folder mới hoặc apply trực tiếp vào game (kèm backup tự động; RPG Maker JSON và Unity XUnity TXT)
6. Tab **Backups**: quản lý backup, xoá memory

## Dịch tự động bằng CLI

```bash
game-translator auto "D:/Games/MyRPG" --provider anthropic --target Vietnamese
```

Detect engine, extract, dịch, tạo `translator_work/translated_data` giữ nguyên cấu trúc folders.

Tham số phổ biến:

```bash
game-translator auto "D:/Games/MyRPG" \
  --provider openai-compatible \
  --model deepseek-r1 \
  --api-base https://your-proxy/v1 \
  --target Vietnamese \
  --in-place           # ghi trực tiếp vào game (auto backup trước)
  --restart            # bỏ qua bản dịch cũ, dịch lại từ đầu
  --glossary terms.csv
  --workers 4
```

Scan trước khi dịch để xem có bao nhiêu text:

```bash
game-translator scan "D:/Games/MyRPG"
```

## CLI workflow chi tiết

```bash
# 1. Extract RPG Maker MV/MZ
game-translator extract rpg-maker "D:/Games/MyRPG" -o work/texts.csv

# 2. Dịch
game-translator translate work/texts.csv -o work/translations.csv --target Vietnamese

# 3. Apply RPG Maker
game-translator apply rpg-maker work/translations.csv --out-dir work/translated_data

# Hoặc full pipeline RPG Maker
game-translator pipeline rpg-maker "D:/Games/MyRPG" --work-dir work --target Vietnamese
```

Tiện ích hậu kỳ:

```bash
# Lint translations CSV (placeholder mismatch, fallback %, noun inconsistency,
# namebox CJK chưa dịch). Có thể lint thêm file glossary.
game-translator validate work/translations.csv --glossary terms.csv --strict

# Dịch lại 1 subset rows tại chỗ (fallback rows, target rỗng, hoặc 1 context cụ thể).
# Hữu ích sau khi chỉnh glossary / correction table.
game-translator retry work/translations.csv --filter fallback --target Vietnamese

# Mang bản dịch sang file texts.csv mới extract (vd: sau khi game patch).
# Output là 1 translations CSV có cột status: new/changed/unchanged/removed.
game-translator diff work/texts_old.csv work/texts_new.csv work/translations.csv \
  -o work/translations_carry.csv
```

Với Unity XUnity, dùng `game-translator auto` hoặc GUI để extract/apply trực tiếp `Translation/{Lang}/Text/*.txt`. Lệnh `extract unity` legacy chỉ quét CSV/TSV/JSON/TXT để export CSV thủ công.

## Glossary

Tạo file CSV `glossary.csv`:

```csv
term,translation,note
勇者,Dũng giả,protagonist
ポーション,Bình thuốc,common item
HP,HP,keep as-is
```

Trong GUI: tab Translate → Advanced → Glossary CSV → Browse.
CLI: `--glossary terms.csv` (hoặc `validate --glossary` để lint file glossary).

Mỗi batch sẽ inject glossary vào system prompt; LLM bắt buộc dịch đúng term.

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

# MTL (free, không cần API key)
game-translator translate work/texts.csv --provider google
game-translator translate work/texts.csv --provider mymemory
```

`bing` cần `MICROSOFT_TRANSLATOR_KEY`; `yandex` cần `YANDEX_TRANSLATE_API_KEY` + `YANDEX_FOLDER_ID`; `libretranslate` có thể trỏ về server riêng qua `LIBRETRANSLATE_URL`.

## Cheat plugin (RPG Maker MV/MZ)

Trong GUI tab Apply → Cheat plugin → Apply Cheat. Tool ưu tiên archive bundled nếu bản build có kèm `vendor/cheat/`, sau đó dùng cache local, cuối cùng mới tải GitHub release. Khi cài, tool copy script vào `www/js/plugins/`, đăng ký vào `plugins.js`, lưu manifest để gỡ sạch sau này.

Toggle trong game: **Ctrl+C**.

## Unity (XUnity AutoTranslator)

Tool dịch các file `Translation/{Lang}/Text/*.txt` mà XUAT generate. Workflow:

1. **Cài XUnity AutoTranslator** vào game (qua BepInEx hoặc MelonLoader). Xem [docs upstream](https://github.com/bbepis/XUnity.AutoTranslator).
2. **Chạy game 1 lần** để XUAT thu thập text → tạo `Translation/{Lang}/Text/_AutoGeneratedTranslations.txt`.
3. **Mở GUI** → chọn folder game → Scan. Tool tự detect `unity-xunity` engine.
4. **Auto-translate** hoặc **Extract+Translate+Export Copy**.
5. Output ở `translator_work/translated_data/{Lang}/Text/*.txt`. Copy vào `Translation/{Lang}/Text/` của game, hoặc dùng "Apply to game" trong GUI để backup rồi copy file TXT vào `Translation/`.
6. **Reload trong game**: nhấn `Alt+R` để XUAT load lại.

CLI:
```bash
game-translator auto "C:/Games/UnityGame" --target Vietnamese
```

Format file:
- `original=translation` (mỗi entry 1 dòng)
- Tool **bảo toàn** `#directives`, `r:"regex"=replacement`, `sr:"splitter"=...` trong các file được xử lý; `*resizer.txt` bị skip
- Chỉ dịch dòng `original=translation` thông thường

## Format CSV

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

`(file, key)` là identity duy nhất — entries cùng `key` ở khác file không bị nhầm lẫn.

## Resume & checkpoint

Translate lưu sau mỗi batch nên có thể dừng/khởi động lại bất cứ lúc nào. Lần sau:
- entries đã có target sẽ không dịch lại
- entries có cùng `source` trong memory → reuse ngay không gọi LLM
- `--restart` để bỏ qua tất cả và dịch lại từ đầu

## An toàn

- GUI tự backup game trước khi apply (`data_backup_<timestamp>`)
- Restore tự tạo `data_before_restore_<timestamp>` trước khi ghi đè
- Tool ghi vào folder riêng (`translator_work/translated_data`); chỉ in-place khi user yêu cầu
- Atomic CSV writes + filelock cho memory tránh corrupt khi parallel/crash
- Không commit `.env` hoặc API keys

## Test

```bash
pip install -e ".[dev]"
pytest
```

143 tests bao quát extract/apply RPG Maker + Unity, cheat plugin, atomic write, concurrent memory save, glossary, translate pipeline, fan-out dedup. 381 tests tổng cộng — thêm PKD_PhoneMenu, MZ control codes, namebox race dedup + per-batch persist, prompt cache split, partial-LLM truncation, glossary linting, và các lệnh diff/retry/validate.

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

Yêu cầu Python 3.10+ và Qt6 runtime libs:

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

Output: `dist/game-translator-gui/game-translator-gui` (binary). Chạy:

```bash
chmod +x dist/game-translator-gui/game-translator-gui
./dist/game-translator-gui/game-translator-gui
```

**Lưu ý**: Linux build phải làm trên máy Linux (không cross-build từ Windows). Dùng GitHub Actions hoặc Docker để CI build cả 2 platform.

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
- `auto.py`: workflow auto cho RPG Maker + Unity
- `rpg_maker_common.py` / `rpg_maker_mv.py` / `rpg_maker_mz.py`: extract + apply MV/MZ
- `rpg_maker_cheat.py`: cài/gỡ Cheat UI Plugin
- `xunity.py`: extract + apply Unity XUnity AutoTranslator format
- `translate_pipeline.py`: retry/dedup/memory/glossary loop dùng chung cho CLI, `auto`, GUI; namebox pre-pass + per-batch persist
- `llm.py`: providers (Anthropic, OpenAI, MTL); glossary injection; dịch tên namebox
- `prompts.py`: rule ngôn ngữ thuần data (không import SDK) — system prompt chia thành block stable + glossary cho Anthropic prompt caching
- `errors.py`: `StoppedByUser` exception để cancel giữa pipeline
- `diff_tool.py`: carry-forward diff giữa 2 file texts.csv extract với translations.csv hiện có
- `csv_store.py`: atomic CSV save/load
- `translation_memory.py`: filelock + atomic memory store
- `glossary.py`: CSV glossary loader, prompt formatter, và `validate_glossary` linter
- `editor.py`: file editor cross-platform
- `unity.py`: extract Unity CSV/JSON candidates (legacy, CLI only — dùng `xunity.py` thay thế)

## Engine support

| Engine | Detect | Extract | Apply | Note |
|---|---|---|---|---|
| RPG Maker MV | ✅ | ✅ | ✅ | đầy đủ |
| RPG Maker MZ | ✅ | ✅ | ✅ | đầy đủ + plugin command 357 |
| Unity (XUnity AutoTranslator) | ✅ | ✅ | ✅ | qua `Translation/{lang}/Text/*.txt` |
| RPG Maker VX Ace / VX / XP | ✅ | ❌ | ❌ | detect only (không hỗ trợ) |

## License

MIT.
