# Game LLM Translator

## Commands

- Install: `pip install -e .`
- Extract RPG Maker: `game-translator extract rpg-maker "D:/Games/Game" -o work/texts.csv`
- Translate: `game-translator translate work/texts.csv --target Vietnamese`
- Apply RPG Maker: `game-translator apply rpg-maker work/translations.csv --out-dir work/translated_data`

## Architecture

- `cli.py`: Typer CLI.
- `rpg_maker.py`: RPG Maker JSON extraction/apply.
- `unity.py`: Unity text candidate extraction.
- `llm.py`: Anthropic/OpenAI providers.
- `csv_store.py`: CSV checkpoint format.

## Notes

Never store API keys in repo. Use `.env` locally.
