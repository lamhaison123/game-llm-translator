from __future__ import annotations

import csv
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QButtonGroup,
    QDialog,
    QHBoxLayout,
    QLabel,
    QPlainTextEdit,
    QPushButton,
    QRadioButton,
    QLineEdit,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QMessageBox,
)

from ..validate import is_cjk_leak, needs_retry


class TranslationEditor(QDialog):
    def __init__(self, parent, path: Path) -> None:
        super().__init__(parent)
        self.path = path
        self.setWindowTitle(f"Review/Edit translations - {path.name}")
        self.resize(1100, 720)
        self.rows: list[dict[str, str]] = []
        self.filtered_indices: list[int] = []
        self.current_index: int | None = None
        self.retry_requested: bool = False
        self._dirty: bool = False

        self._build()
        self._load()

    def _build(self) -> None:
        layout = QVBoxLayout(self)

        # Filter bar
        bar = QHBoxLayout()
        bar.addWidget(QLabel("Filter:"))
        self.filter_group = QButtonGroup(self)
        for label, value in [
            ("All", "all"),
            ("Untranslated/Fallback", "fallback"),
            ("CJK leak", "cjk_leak"),
            ("Needs retry", "needs_retry"),
            ("Translated", "translated"),
        ]:
            rb = QRadioButton(label)
            rb.setProperty("filter_value", value)
            if value == "all":
                rb.setChecked(True)
            rb.toggled.connect(self._apply_filter)
            bar.addWidget(rb)
            self.filter_group.addButton(rb)
        bar.addWidget(QLabel("Search:"))
        self.search_edit = QLineEdit()
        self.search_edit.returnPressed.connect(self._apply_filter)
        bar.addWidget(self.search_edit, 1)
        go = QPushButton("Go")
        go.clicked.connect(self._apply_filter)
        bar.addWidget(go)
        self.count_label = QLabel("")
        bar.addWidget(self.count_label)
        layout.addLayout(bar)

        # Tree
        self.tree = QTreeWidget()
        self.tree.setHeaderLabels(["#", "File", "Key", "Source", "Target"])
        self.tree.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.tree.setRootIsDecorated(False)
        self.tree.setAlternatingRowColors(True)
        self.tree.itemSelectionChanged.connect(self._on_select)
        layout.addWidget(self.tree, 3)

        # Source/target editors
        editors = QHBoxLayout()
        sv = QVBoxLayout()
        sv.addWidget(QLabel("Source"))
        self.source_box = QPlainTextEdit()
        self.source_box.setReadOnly(True)
        sv.addWidget(self.source_box)
        editors.addLayout(sv)
        tv = QVBoxLayout()
        tv.addWidget(QLabel("Target"))
        self.target_box = QPlainTextEdit()
        tv.addWidget(self.target_box)
        editors.addLayout(tv)
        layout.addLayout(editors, 2)

        # Buttons
        btns = QHBoxLayout()
        save_row = QPushButton("Save Current Row")
        save_row.clicked.connect(lambda: self._save_current(update_tree=True))
        btns.addWidget(save_row)
        save_csv = QPushButton("Save CSV")
        save_csv.clicked.connect(self._save_file)
        btns.addWidget(save_csv)
        copy_src = QPushButton("Use Source as Translation")
        copy_src.clicked.connect(self._copy_source)
        btns.addWidget(copy_src)
        retry_btn = QPushButton("Save && Retry flagged rows")
        retry_btn.setToolTip("Save CSV then re-translate fallback + CJK-leak rows using current Translate tab settings.")
        retry_btn.clicked.connect(self._retry_flagged)
        btns.addWidget(retry_btn)
        btns.addStretch()
        close = QPushButton("Close")
        close.clicked.connect(self.close)
        btns.addWidget(close)
        layout.addLayout(btns)

    def _load(self) -> None:
        with self.path.open("r", newline="", encoding="utf-8-sig") as fp:
            self.rows = [dict(row) for row in csv.DictReader(fp)]
        self._apply_filter()
        if self.filtered_indices:
            first_item = self.tree.topLevelItem(0)
            if first_item is not None:
                self.tree.setCurrentItem(first_item)

    def _is_fallback(self, row: dict[str, str]) -> bool:
        src = row.get("source", "").strip()
        tgt = row.get("target", "").strip()
        return not tgt or tgt == src

    def _has_cjk_leak(self, row: dict[str, str]) -> bool:
        src = row.get("source", "")
        tgt = row.get("target", "")
        if not tgt.strip() or tgt == src:
            return False
        return is_cjk_leak(tgt)

    def _needs_retry(self, row: dict[str, str]) -> bool:
        return needs_retry(row.get("source", ""), row.get("target", ""))

    def _apply_filter(self) -> None:
        self._save_current(update_tree=False)
        mode = "all"
        for btn in self.filter_group.buttons():
            if btn.isChecked():
                mode = btn.property("filter_value")
                break
        search = self.search_edit.text().lower()
        self.tree.clear()
        self.filtered_indices = []
        for i, row in enumerate(self.rows):
            if mode == "fallback" and not self._is_fallback(row):
                continue
            if mode == "cjk_leak" and not self._has_cjk_leak(row):
                continue
            if mode == "needs_retry" and not self._needs_retry(row):
                continue
            if mode == "translated" and self._is_fallback(row):
                continue
            if search and search not in (row.get("source", "") + row.get("target", "") + row.get("key", "")).lower():
                continue
            item = QTreeWidgetItem([str(i), row.get("file", ""), row.get("key", ""), row.get("source", ""), row.get("target", "")])
            item.setData(0, Qt.ItemDataRole.UserRole, i)
            color = self._row_color(row)
            if color is not None:
                for col in range(5):
                    item.setForeground(col, color)
            self.tree.addTopLevelItem(item)
            self.filtered_indices.append(i)
        fb = sum(1 for r in self.rows if self._is_fallback(r))
        leak = sum(1 for r in self.rows if self._has_cjk_leak(r))
        self.count_label.setText(f"Showing {len(self.filtered_indices)}/{len(self.rows)} | Fallback: {fb} | CJK leak: {leak}")
        self.current_index = None

    def _row_color(self, row: dict[str, str]) -> QColor | None:
        if self._is_fallback(row):
            return QColor(204, 68, 0)  # orange — fallback/empty
        if self._has_cjk_leak(row):
            return QColor(176, 0, 32)  # red — partial CJK leak
        return None

    def _on_select(self) -> None:
        items = self.tree.selectedItems()
        if not items:
            return
        if self.current_index is not None:
            self._save_current(update_tree=True)
        self.current_index = int(items[0].data(0, Qt.ItemDataRole.UserRole))
        row = self.rows[self.current_index]
        self.source_box.setPlainText(row.get("source", ""))
        self.target_box.setPlainText(row.get("target", ""))

    def _save_current(self, update_tree: bool) -> None:
        if self.current_index is None:
            return
        row = self.rows[self.current_index]
        new_target = self.target_box.toPlainText().rstrip("\n")
        old_target = row.get("target", "")
        if new_target != old_target.rstrip("\n"):
            row["target"] = new_target
            self._dirty = True
        else:
            # Keep the stored value verbatim, but ensure the key exists so the
            # tree-update read below (and _save_to_disk) can never KeyError.
            row.setdefault("target", old_target)
        if update_tree:
            for i in range(self.tree.topLevelItemCount()):
                it = self.tree.topLevelItem(i)
                if it is not None and int(it.data(0, Qt.ItemDataRole.UserRole)) == self.current_index:
                    it.setText(4, row["target"])
                    color = self._row_color(row) or self.tree.palette().text().color()
                    for c in range(5):
                        it.setForeground(c, color)
                    break

    def _copy_source(self) -> None:
        self.target_box.setPlainText(self.source_box.toPlainText())
        self._save_current(update_tree=True)

    def _save_file(self) -> None:
        self._save_to_disk()
        QMessageBox.information(self, "Save CSV", f"Saved {self.path}")

    def _save_to_disk(self) -> None:
        self._save_current(update_tree=True)
        fieldnames = ["file", "key", "source", "target", "context", "sub_keys"]
        for row in self.rows:
            for k in row:
                if k is not None and k not in fieldnames:
                    fieldnames.append(k)
        with self.path.open("w", newline="", encoding="utf-8") as fp:
            w = csv.DictWriter(fp, fieldnames=fieldnames)
            w.writeheader()
            for row in self.rows:
                w.writerow({n: row.get(n, "") for n in fieldnames})
        self._dirty = False

    def _confirm_close(self) -> bool:
        """Flush the current row and, if edits are unsaved, prompt to save.

        Returns True if the dialog may close, False to keep it open.
        """
        self._save_current(update_tree=False)
        if not self._dirty:
            return True
        reply = QMessageBox.question(
            self, "Unsaved changes",
            "You have unsaved edits. Save to CSV before closing?",
            QMessageBox.StandardButton.Save
            | QMessageBox.StandardButton.Discard
            | QMessageBox.StandardButton.Cancel,
        )
        if reply == QMessageBox.StandardButton.Cancel:
            return False
        if reply == QMessageBox.StandardButton.Save:
            try:
                self._save_to_disk()
            except OSError as exc:
                QMessageBox.warning(self, "Save CSV", f"Could not save: {exc}")
                return False
        return True

    def closeEvent(self, event) -> None:
        if self._confirm_close():
            event.accept()
        else:
            event.ignore()

    def reject(self) -> None:
        # Esc / reject() bypasses closeEvent under exec(); route through the same guard.
        if self._confirm_close():
            super().reject()

    def _retry_flagged(self) -> None:
        self._save_current(update_tree=True)
        flagged = sum(1 for r in self.rows if self._needs_retry(r))
        if flagged == 0:
            QMessageBox.information(self, "Retry flagged", "No fallback or CJK-leak rows to retry.")
            return
        reply = QMessageBox.question(
            self,
            "Retry flagged rows",
            f"Save and re-translate {flagged} flagged row(s) using current Translate tab settings?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return
        self._save_to_disk()
        self.retry_requested = True
        self.accept()