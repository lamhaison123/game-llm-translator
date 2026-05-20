# Game LLM Translator

## Commands

- Install: `pip install -e .`
- Extract RPG Maker: `game-translator extract rpg-maker "D:/Games/Game" -o work/texts.csv`
- Translate: `game-translator translate work/texts.csv --target Vietnamese --glossary terms.csv --workers 4`
- Apply RPG Maker: `game-translator apply rpg-maker work/translations.csv --out-dir work/translated_data`

## Architecture

- `cli.py`: Typer CLI.
- `translate_pipeline.py`: Shared translate loop (retry, memory, dedup, glossary).
- `rpg_maker.py`: RPG Maker JSON extraction/apply.
- `unity.py`: Unity text candidate extraction.
- `xunity.py`: XUnity AutoTranslator TXT extract/apply.
- `llm.py`: Anthropic/OpenAI/MTL providers.
- `csv_store.py`: CSV checkpoint format.
- `gui.py`: PySide6 desktop UI.

## Notes

Never store API keys in repo. Use `.env` locally.
