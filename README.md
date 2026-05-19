# Game LLM Translator

Tool dịch text game RPG Maker MV/MZ bằng LLM hoặc MTL, có GUI desktop với progress bar, dark mode, glossary, parallel workers và retry tự động.

## Tính năng chính

- **RPG Maker MV/MZ**: extract `www/data/*.json` (hoặc `data/*.json`), dịch, apply giữ nguyên cấu trúc nested folders
- **Parallel translate** với 1-8 workers, batch size auto hoặc tự chỉnh
- **Retry thông minh**: đọc `retry_after` từ Cloudflare 524, defer batch failed → retry cuối job với delay dài hơn, fallback source nếu vẫn fail
- **Translation memory**: per-game + global, file-locked để tránh corrupt khi parallel write
- **Atomic CSV writes**: tmp file + `os.replace`, an toàn khi crash giữa lúc save
- **Glossary CSV**: đặt term/translation cố định, inject vào system prompt mỗi batch
- **Multi-provider**: Anthropic Claude, OpenAI/OpenAI-compatible (OpenRouter, LM Studio, Ollama), Google MTL, MyMemory, LibreTranslate, Microsoft, Yandex
- **Cheat plugin**: cài/gỡ RPG Maker MV/MZ Cheat UI Plugin từ GitHub release
- **GUI desktop**:
  - Light/Dark mode toggle (sv-ttk theme)
  - Progress bar với % và ETA real-time
  - Editor với filter (All / Untranslated+Fallback / Translated) + search, fallback rows highlight đỏ
  - Backup tab: tạo/restore/xóa multi-select; clear game/global memory; clear old translation
  - Stop button interrupt API call trong vòng 0.5s

## Cài đặt

```bash
git clone https://github.com/lamhaison123/game-llm-translator
cd game-llm-translator
python -m venv .venv
.venv\Scripts\activate     # Windows
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
1. Tab **Game**: chọn thư mục game → Scan → Auto-translate hoặc Extract+Translate+Export
2. Tab **Provider**: chọn provider, model, API key/base URL
3. Tab **Translate**: tinh chỉnh batch size, workers, glossary, memory
4. Tab **Review**: edit translations.csv với filter/search
5. Tab **Apply**: export sang folder mới hoặc apply trực tiếp vào game (kèm backup tự động)
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
```

Scan trước khi dịch để xem có bao nhiêu text:

```bash
game-translator scan "D:/Games/MyRPG"
```

## CLI workflow chi tiết

```bash
# 1. Extract
game-translator extract rpg-maker "D:/Games/MyRPG" -o work/texts.csv

# 2. Dịch
game-translator translate work/texts.csv -o work/translations.csv --target Vietnamese

# 3. Apply
game-translator apply rpg-maker work/translations.csv --out-dir work/translated_data

# Hoặc full pipeline
game-translator pipeline rpg-maker "D:/Games/MyRPG" --work-dir work --target Vietnamese
```

## Glossary

Tạo file CSV `glossary.csv`:

```csv
term,translation,note
勇者,Dũng giả,protagonist
ポーション,Bình thuốc,common item
HP,HP,keep as-is
```

Trong GUI: tab Translate → Advanced → Glossary CSV → Browse.
Hoặc CLI: `--glossary path/to/glossary.csv`.

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

Trong GUI tab Game → Cheat plugin → Apply Cheat. Tool tải release từ GitHub, copy script vào `www/js/plugins/`, đăng ký vào `plugins.js`, lưu manifest để gỡ sạch sau này.

Toggle trong game: **Ctrl+C**.

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

118 tests bao quát extract/apply, cheat plugin, atomic write, concurrent memory save, glossary, translate pipeline.

## Build Windows exe

```powershell
pyinstaller --noconfirm --windowed --name game-translator-gui `
  --paths src --distpath dist --workpath build `
  gui_launcher.py
```

## Architecture

- `cli.py`: Typer CLI (auto, scan, extract, translate, apply, pipeline, edit)
- `gui.py`: Tkinter GUI với sv-ttk theme
- `auto.py`: workflow auto cho RPG Maker
- `rpg_maker_common.py` / `rpg_maker_mv.py` / `rpg_maker_mz.py`: extract + apply MV/MZ
- `rpg_maker_cheat.py`: cài/gỡ Cheat UI Plugin
- `llm.py`: providers (Anthropic, OpenAI, MTL); language-aware system prompt; glossary injection
- `csv_store.py`: atomic CSV save/load
- `translation_memory.py`: filelock + atomic memory store
- `glossary.py`: CSV glossary loader + prompt formatter
- `editor.py`: file editor cross-platform
- `unity.py`: extract Unity CSV/JSON candidates (CLI only, GUI chưa wire)

## Engine support

| Engine | Detect | Extract | Apply | Note |
|---|---|---|---|---|
| RPG Maker MV | ✅ | ✅ | ✅ | đầy đủ |
| RPG Maker MZ | ✅ | ✅ | ✅ | đầy đủ + plugin command 357 |
| RPG Maker VX Ace | ✅ | ❌ | ❌ | detect only (cần parse `.rvdata2`) |
| RPG Maker VX / XP | ✅ | ❌ | ❌ | detect only |
| Unity | ⚠️ | ⚠️ | ⚠️ | CSV/JSON/TXT layer; chưa wire vào GUI |

## License

MIT.
