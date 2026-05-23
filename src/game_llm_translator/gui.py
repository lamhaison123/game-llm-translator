from __future__ import annotations

# Thin facade — keeps backward compatibility for:
#   pyproject.toml: game-translator-gui = "game_llm_translator.gui:main"
#   gui_launcher.py: from game_llm_translator.gui import main
from .gui import TranslatorGUI, WorkerSignals, TranslationEditor, main  # noqa: F401