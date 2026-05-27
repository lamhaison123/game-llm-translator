# Game LLM Translator

## Commands

- Install: `pip install -e .`
- Extract RPG Maker: `game-translator extract rpg-maker "D:/Games/Game" -o work/texts.csv`
- Translate: `game-translator translate work/texts.csv --target Vietnamese --glossary terms.csv --workers 4`
- Apply RPG Maker: `game-translator apply rpg-maker work/translations.csv --out-dir work/translated_data`

## Architecture

- `cli.py`: Typer CLI (auto, scan, extract, translate, apply, pipeline, edit, diff, retry, validate).
- `translate_pipeline.py`: Shared translate loop (retry, memory, dedup, glossary, namebox pre-pass + persist).
- `rpg_maker.py`: Facade re-exporting `rpg_maker_common` / `_mv` / `_mz` extract+apply.
- `unity.py`: Unity text candidate extraction (legacy).
- `xunity.py`: XUnity AutoTranslator TXT extract/apply.
- `llm.py`: Anthropic/OpenAI/MTL providers + namebox name translation.
- `prompts.py`: Pure-data language rules + system prompt builder (no SDK imports).
- `glossary.py`: Glossary loader, prompt formatter, and `validate_glossary` linter.
- `diff_tool.py`: Carry-forward diff between extracts.
- `errors.py`: `StoppedByUser` exception for mid-pipeline cancel.
- `csv_store.py`: CSV checkpoint format (atomic writes).
- `gui.py`: PySide6 desktop UI.

## Notes

Never store API keys in repo. Use `.env` locally.
