from __future__ import annotations

from .main_window import TranslatorGUI
from .signals import WorkerSignals
from .editor import TranslationEditor


def main() -> None:
    import sys
    from PySide6.QtWidgets import QApplication
    app = QApplication(sys.argv)
    app.setStyle("Fusion")
    win = TranslatorGUI()
    win.show()
    sys.exit(app.exec())