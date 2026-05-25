from __future__ import annotations

from pathlib import Path

from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QVBoxLayout,
    QWidget,
)

from ..editor import TranslationEditor
from ...editor import open_file_editor


class ReviewTabMixin:
    translations_csv_edit: QLineEdit

    def _build_review_tab(self) -> QWidget:
        tab = QWidget()
        outer = QVBoxLayout(tab)
        info = QLabel("Edit translations.csv: filter, search, mark fallback rows. Open external editor for raw CSV.")
        info.setWordWrap(True)
        outer.addWidget(info)

        row = QHBoxLayout()
        row.addWidget(self._action_button("Review/Edit Translations", self.edit_table))
        row.addWidget(self._action_button("Open CSV Externally", self.edit_csv))
        row.addStretch()
        outer.addLayout(row)
        outer.addStretch()
        return tab

    def edit_table(self) -> None:
        path = Path(self.translations_csv_edit.text())
        if not path.exists():
            QMessageBox.warning(self, "Review/Edit", f"File not found: {path}")
            return
        dlg = TranslationEditor(self, path)
        dlg.exec()

    def edit_csv(self) -> None:
        path = Path(self.translations_csv_edit.text())
        if not path.exists():
            QMessageBox.warning(self, "Open CSV", f"File not found: {path}")
            return
        open_file_editor(path)