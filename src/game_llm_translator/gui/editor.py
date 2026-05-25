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


class TranslationEditor(QDialog):
    def __init__(self, parent, path: Path) -> None:
        super().__init__(parent)
        self.path = path
        self.setWindowTitle(f"Review/Edit translations - {path.name}")
        self.resize(1100, 720)
        self.rows: list[dict[str, str]] = []
        self.filtered_indices: list[int] = []
        self.current_index: int | None = None

        self._build()
        self._load()

    def _build(self) -> None:
        layout = QVBoxLayout(self)

        # Filter bar
        bar = QHBoxLayout()
        bar.addWidget(QLabel("Filter:"))
        self.filter_group = QButtonGroup(self)
        for label, value in [("All", "all"), ("Untranslated/Fallback", "fallback"), ("Translated", "translated")]:
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
            if mode == "translated" and self._is_fallback(row):
                continue
            if search and search not in (row.get("source", "") + row.get("target", "") + row.get("key", "")).lower():
                continue
            item = QTreeWidgetItem([str(i), row.get("file", ""), row.get("key", ""), row.get("source", ""), row.get("target", "")])
            item.setData(0, Qt.ItemDataRole.UserRole, i)
            if self._is_fallback(row):
                for col in range(5):
                    item.setForeground(col, QColor(204, 68, 0))
            self.tree.addTopLevelItem(item)
            self.filtered_indices.append(i)
        fb = sum(1 for r in self.rows if self._is_fallback(r))
        self.count_label.setText(f"Showing {len(self.filtered_indices)}/{len(self.rows)} | Fallback: {fb}")
        self.current_index = None

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
        row["target"] = self.target_box.toPlainText().rstrip("\n")
        if update_tree:
            for i in range(self.tree.topLevelItemCount()):
                it = self.tree.topLevelItem(i)
                if it is not None and int(it.data(0, Qt.ItemDataRole.UserRole)) == self.current_index:
                    it.setText(4, row["target"])
                    color = QColor(204, 68, 0) if self._is_fallback(row) else self.tree.palette().text().color()
                    for c in range(5):
                        it.setForeground(c, color)
                    break

    def _copy_source(self) -> None:
        self.target_box.setPlainText(self.source_box.toPlainText())
        self._save_current(update_tree=True)

    def _save_file(self) -> None:
        self._save_current(update_tree=True)
        fieldnames = ["file", "key", "source", "target", "context", "sub_keys"]
        with self.path.open("w", newline="", encoding="utf-8") as fp:
            w = csv.DictWriter(fp, fieldnames=fieldnames)
            w.writeheader()
            for row in self.rows:
                w.writerow({n: row.get(n, "") for n in fieldnames})
        QMessageBox.information(self, "Save CSV", f"Saved {self.path}")