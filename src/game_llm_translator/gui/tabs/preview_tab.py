from __future__ import annotations

import csv
import re
import threading
from pathlib import Path
from typing import TYPE_CHECKING, Callable

if TYPE_CHECKING:
    from ..signals import WorkerSignals

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QAbstractItemView,
    QButtonGroup,
    QComboBox,
    QFileDialog,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPlainTextEdit,
    QPushButton,
    QRadioButton,
    QSpinBox,
    QCheckBox,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from ...csv_store import load_entries, load_results, save_results
from ...editor import open_file_editor
from ...llm import _context_hint
from ...models import TextEntry, TranslationResult, text_identity
from ...translate_pipeline import TranslateOptions, run_translate
from ...validate import translation_warnings
from ..editor import TranslationEditor
from ..paths import normalize_path_text


_PREVIEW_STATUS_DONE = "done"
_PREVIEW_STATUS_WARN = "warn"
_PREVIEW_STATUS_FALLBACK = "fallback"
_PREVIEW_STATUS_PENDING = "pending"

_STATUS_ICONS = {
    _PREVIEW_STATUS_DONE: "✓",
    _PREVIEW_STATUS_WARN: "⚠",
    _PREVIEW_STATUS_FALLBACK: "○",
    _PREVIEW_STATUS_PENDING: "…",
}


class PreviewRow:
    __slots__ = ("idx", "file", "key", "source", "target", "context", "sub_keys", "manually_edited", "warnings")

    def __init__(self, idx: int, file: str, key: str, source: str, target: str, context: str, sub_keys: list[str] | None = None) -> None:
        self.idx = idx
        self.file = file
        self.key = key
        self.source = source
        self.target = target
        self.context = context
        self.sub_keys = sub_keys or []
        self.manually_edited = False
        self.warnings: list[str] = []

    def status(self) -> str:
        if self.warnings:
            return _PREVIEW_STATUS_WARN
        if not self.target.strip() or self.target == self.source:
            return _PREVIEW_STATUS_FALLBACK
        return _PREVIEW_STATUS_DONE

    def compute_warnings(self) -> None:
        self.warnings = translation_warnings(self.source, self.target, self.context)


class PreviewTabMixin:
    signals: WorkerSignals
    config: dict
    stop_requested: threading.Event
    provider_combo: QComboBox
    model_edit: QLineEdit
    api_key_edit: QLineEdit
    api_base_edit: QLineEdit
    source_lang_edit: QLineEdit
    target_lang_edit: QLineEdit
    batch_size_spin: QSpinBox
    workers_spin: QSpinBox
    glossary_path_edit: QLineEdit
    correction_table_path_edit: QLineEdit
    reuse_memory_check: QCheckBox
    save_memory_check: QCheckBox
    _log: object
    _check_stopped: object
    _run: object
    _path_picker: object
    _action_button: object
    _safe_button: object
    # Provided by TranslateTabMixin (sibling on TranslatorGUI):
    retry_flagged_rows: Callable[[], None]

    def _build_preview_tab(self) -> QWidget:
        tab = QWidget()
        outer = QVBoxLayout(tab)

        # --- Bulk editor (from old Review tab) ---
        bulk = QGroupBox("Bulk editor")
        bl = QHBoxLayout(bulk)
        bl.addWidget(self._action_button("Open Bulk Editor (translations.csv)", self._open_bulk_editor))
        bl.addWidget(self._safe_button("Open CSV Externally", self._open_translations_csv_externally))
        bl.addStretch()
        outer.addWidget(bulk)

        # --- Config section ---
        cfg = QGroupBox("Translation settings")
        form = QFormLayout(cfg)

        self.preview_source_edit = QLineEdit(normalize_path_text(str(self.config.get("preview_source", ""))))
        form.addRow("Source CSV:", self._path_picker(self.preview_source_edit, self._choose_preview_source))

        self.preview_output_edit = QLineEdit(normalize_path_text(str(self.config.get("preview_output", ""))))
        form.addRow("Output CSV:", self._path_picker(self.preview_output_edit, self._choose_preview_output))

        row_lang = QHBoxLayout()
        self.preview_target_edit = QLineEdit(self.target_lang_edit.text() or "Vietnamese")
        row_lang.addWidget(self.preview_target_edit)
        form.addRow("Target lang:", row_lang)

        row_prov = QHBoxLayout()
        self.preview_provider_combo = QComboBox()
        for i in range(self.provider_combo.count()):
            self.preview_provider_combo.addItem(self.provider_combo.itemText(i))
        self.preview_provider_combo.setCurrentText(self.provider_combo.currentText())
        row_prov.addWidget(self.preview_provider_combo)
        self.preview_model_edit = QLineEdit(self.model_edit.text())
        row_prov.addWidget(self.preview_model_edit, 1)
        form.addRow("Provider / Model:", row_prov)

        self.preview_workers_spin = QSpinBox()
        self.preview_workers_spin.setRange(1, 8)
        self.preview_workers_spin.setValue(int(self.config.get("workers", 1)))
        form.addRow("Workers:", self.preview_workers_spin)

        self.preview_restart_check = QCheckBox("Ignore existing translations and start over")
        self.preview_restart_check.setChecked(True)
        form.addRow("", self.preview_restart_check)

        outer.addWidget(cfg)

        # --- Action buttons ---
        actions = QHBoxLayout()
        self.preview_start_btn = self._action_button("Start Translation", self._preview_start)
        self.preview_stop_btn = self._safe_button("Stop", self._preview_stop)
        self.preview_stop_btn.setEnabled(False)
        self.preview_save_btn = self._safe_button("Save CSV", self._preview_save)
        self.preview_load_btn = self._safe_button("Load CSV", self._preview_load)
        actions.addWidget(self.preview_start_btn)
        actions.addWidget(self.preview_stop_btn)
        actions.addWidget(self.preview_save_btn)
        actions.addWidget(self.preview_load_btn)
        actions.addStretch()
        outer.addLayout(actions)

        # --- Progress ---
        prog = QHBoxLayout()
        self.preview_progress_label = QLabel("")
        prog.addWidget(self.preview_progress_label)
        prog.addStretch()
        outer.addLayout(prog)

        # --- Filters ---
        filters = QHBoxLayout()
        filters.addWidget(QLabel("Filter:"))
        self.preview_filter_group = QButtonGroup(self)
        for label, value in [("All", "all"), ("Untranslated", "fallback"), ("Warnings", "warn")]:
            rb = QRadioButton(label)
            rb.setProperty("filter_value", value)
            if value == "all":
                rb.setChecked(True)
            rb.toggled.connect(self._preview_apply_filter)
            filters.addWidget(rb)
            self.preview_filter_group.addButton(rb)

        filters.addWidget(QLabel("Context:"))
        self.preview_context_combo = QComboBox()
        self.preview_context_combo.addItem("All")
        self.preview_context_combo.currentIndexChanged.connect(self._preview_apply_filter)
        filters.addWidget(self.preview_context_combo)

        filters.addWidget(QLabel("Search:"))
        self.preview_search_edit = QLineEdit()
        self.preview_search_edit.setPlaceholderText("source, target, or key...")
        self.preview_search_edit.returnPressed.connect(self._preview_apply_filter)
        filters.addWidget(self.preview_search_edit, 1)

        self.preview_count_label = QLabel("")
        filters.addWidget(self.preview_count_label)
        outer.addLayout(filters)

        bulk = QHBoxLayout()
        bulk.addWidget(QLabel("Bulk replace target:"))
        self.preview_bulk_find_edit = QLineEdit()
        self.preview_bulk_find_edit.setPlaceholderText("Find...")
        bulk.addWidget(self.preview_bulk_find_edit, 1)
        self.preview_bulk_replace_edit = QLineEdit()
        self.preview_bulk_replace_edit.setPlaceholderText("Replace with...")
        bulk.addWidget(self.preview_bulk_replace_edit, 1)
        self.preview_bulk_case_check = QCheckBox("Case sensitive")
        self.preview_bulk_case_check.setChecked(True)
        bulk.addWidget(self.preview_bulk_case_check)
        bulk_btn = QPushButton("Replace in Filtered Rows")
        bulk_btn.clicked.connect(self._preview_confirm_bulk_replace)
        bulk.addWidget(bulk_btn)
        outer.addLayout(bulk)

        # --- Table ---
        self.preview_tree = QTreeWidget()
        self.preview_tree.setHeaderLabels(["#", "File", "Key", "Source", "Target", "Status"])
        self.preview_tree.setColumnWidth(0, 40)
        self.preview_tree.setColumnWidth(1, 100)
        self.preview_tree.setColumnWidth(2, 100)
        self.preview_tree.setColumnWidth(3, 200)
        self.preview_tree.setColumnWidth(4, 200)
        self.preview_tree.setColumnWidth(5, 40)
        self.preview_tree.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.preview_tree.setRootIsDecorated(False)
        self.preview_tree.setAlternatingRowColors(True)
        self.preview_tree.itemSelectionChanged.connect(self._preview_on_select)
        outer.addWidget(self.preview_tree, 3)

        # --- Editors ---
        editors = QHBoxLayout()
        sv = QVBoxLayout()
        sv.addWidget(QLabel("Source"))
        self.preview_source_box = QPlainTextEdit()
        self.preview_source_box.setReadOnly(True)
        self.preview_source_box.setMaximumHeight(80)
        sv.addWidget(self.preview_source_box)
        editors.addLayout(sv)
        tv = QVBoxLayout()
        tv.addWidget(QLabel("Target"))
        self.preview_target_box = QPlainTextEdit()
        self.preview_target_box.setMaximumHeight(80)
        tv.addWidget(self.preview_target_box)
        self.preview_warning_label = QLabel("")
        self.preview_warning_label.setStyleSheet("color: #b07000;")
        self.preview_warning_label.setWordWrap(True)
        tv.addWidget(self.preview_warning_label)
        editors.addLayout(tv)
        outer.addLayout(editors, 2)

        # --- Editor buttons ---
        ebtns = QHBoxLayout()
        save_row = QPushButton("Save Row")
        save_row.clicked.connect(self._preview_save_current)
        ebtns.addWidget(save_row)
        copy_src = QPushButton("Use Source")
        copy_src.clicked.connect(self._preview_copy_source)
        ebtns.addWidget(copy_src)
        next_warn = QPushButton("Next Warning")
        next_warn.clicked.connect(self._preview_next_warning)
        ebtns.addWidget(next_warn)
        ebtns.addStretch()
        outer.addLayout(ebtns)

        # --- State ---
        self._preview_rows: list[PreviewRow] = []
        self._preview_filtered: list[int] = []  # indices into _preview_rows
        self._preview_entry_to_row: dict[tuple[str, str], int] = {}
        self._preview_current_idx: int | None = None
        self._preview_dirty: bool = False

        # Connect signals
        self.signals.result_batch.connect(self._preview_on_batch)

        return tab

    # ------------------------------------------------------------------
    # File choosers
    # ------------------------------------------------------------------

    def _choose_preview_source(self) -> None:
        value, _ = QFileDialog.getOpenFileName(
            self, "Select source CSV", self.preview_source_edit.text(),
            "CSV files (*.csv);;All files (*)",
        )
        if value:
            self.preview_source_edit.setText(normalize_path_text(value))

    def _open_bulk_editor(self) -> None:
        """Open the modal TranslationEditor on the translations.csv from the Setup tab."""
        from PySide6.QtWidgets import QMessageBox
        path = Path(self.translations_csv_edit.text())
        if not path.exists():
            QMessageBox.warning(self, "Review/Edit", f"File not found: {path}")
            return
        dlg = TranslationEditor(self, path)
        dlg.exec_()
        if getattr(dlg, "retry_requested", False):
            self.retry_flagged_rows()

    def _open_translations_csv_externally(self) -> None:
        from PySide6.QtWidgets import QMessageBox
        path = Path(self.translations_csv_edit.text())
        if not path.exists():
            QMessageBox.warning(self, "Open CSV", f"File not found: {path}")
            return
        open_file_editor(path)

    def _choose_preview_output(self) -> None:
        value, _ = QFileDialog.getOpenFileName(
            self, "Select output CSV", self.preview_output_edit.text(),
            "CSV files (*.csv);;All files (*)",
        )
        if value:
            self.preview_output_edit.setText(normalize_path_text(value))

    # ------------------------------------------------------------------
    # Load existing CSV
    # ------------------------------------------------------------------

    def _preview_load(self) -> None:
        if self._preview_dirty:
            from PySide6.QtWidgets import QMessageBox
            reply = QMessageBox.question(
                self, "Unsaved changes",
                "You have unsaved edits. Load anyway?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            )
            if reply != QMessageBox.StandardButton.Yes:
                return

        path = self.preview_source_edit.text().strip()
        if not path:
            # Try output CSV as fallback
            path = self.preview_output_edit.text().strip()
        if not path:
            from PySide6.QtWidgets import QMessageBox
            QMessageBox.warning(self, "Load CSV", "No CSV path specified.")
            return
        csv_path = Path(path)
        if not csv_path.exists():
            from PySide6.QtWidgets import QMessageBox
            QMessageBox.warning(self, "Load CSV", f"File not found: {csv_path}")
            return

        try:
            results = load_results(csv_path)
        except Exception as exc:
            from PySide6.QtWidgets import QMessageBox
            QMessageBox.warning(self, "Load CSV", f"Failed to load: {exc}")
            return

        self._preview_rows = []
        self._preview_filtered = []
        self._preview_entry_to_row = {}
        self._preview_current_idx = None
        self.preview_tree.clear()
        self.preview_source_box.clear()
        self.preview_target_box.clear()
        self.preview_warning_label.clear()
        for i, r in enumerate(results):
            row = PreviewRow(i, str(r.file), r.key, r.source, r.target, r.context, r.sub_keys)
            self._preview_rows.append(row)
            self._preview_entry_to_row[text_identity(Path(row.file), row.key)] = row.idx
        self._preview_dirty = False
        self._preview_populate_contexts()
        self._preview_apply_filter()
        self._preview_update_progress()

    # ------------------------------------------------------------------
    # Start translation
    # ------------------------------------------------------------------

    def _preview_start(self) -> None:
        source_path = self.preview_source_edit.text().strip()
        if not source_path:
            from PySide6.QtWidgets import QMessageBox
            QMessageBox.warning(self, "Preview", "Select a source CSV first.")
            return
        source = Path(source_path)
        if not source.exists():
            from PySide6.QtWidgets import QMessageBox
            QMessageBox.warning(self, "Preview", f"Source CSV not found: {source}")
            return

        output_path = self.preview_output_edit.text().strip()
        if not output_path:
            output_path = str(source.parent / "translations.csv")
            self.preview_output_edit.setText(output_path)

        try:
            entries = load_entries(source)
        except Exception as exc:
            from PySide6.QtWidgets import QMessageBox
            QMessageBox.warning(self, "Preview", f"Failed to load entries: {exc}")
            return

        if not entries:
            from PySide6.QtWidgets import QMessageBox
            QMessageBox.warning(self, "Preview", "No entries found in source CSV.")
            return

        # Reset rows — mark all as pending
        self._preview_rows = []
        for i, e in enumerate(entries):
            row = PreviewRow(i, str(e.file), e.key, e.source, "", e.context)
            self._preview_rows.append(row)
        self._preview_dirty = False
        self._preview_populate_contexts()
        self._preview_apply_filter()

        # Track which entries map to which rows for batch result insertion
        self._preview_entry_to_row: dict[tuple[str, str], int] = {}
        for row in self._preview_rows:
            self._preview_entry_to_row[text_identity(Path(row.file), row.key)] = row.idx

        output = Path(output_path)
        gp = self.glossary_path_edit.text().strip()
        cp = self.correction_table_path_edit.text().strip()

        options = TranslateOptions(
            target_lang=self.preview_target_edit.text(),
            source_lang=None if self.source_lang_edit.text().lower() == "auto" else self.source_lang_edit.text(),
            provider=self.preview_provider_combo.currentText(),
            model=self.preview_model_edit.text() or self.preview_provider_combo.currentText(),
            api_key=self.api_key_edit.text().strip() or None,
            api_base=self.api_base_edit.text().strip() or None,
            batch_size=int(self.batch_size_spin.value()),
            workers=max(1, min(8, int(self.preview_workers_spin.value()))),
            use_memory=self.reuse_memory_check.isChecked(),
            save_memory=self.save_memory_check.isChecked(),
            glossary_path=Path(gp) if gp else None,
            correction_table_path=Path(cp) if cp else None,
            restart=self.preview_restart_check.isChecked(),
            on_log=self._log,  # type: ignore[assignment]
            stop_event=self.stop_requested,
            on_batch_results=lambda batch: self.signals.result_batch.emit(batch),
        )

        # Check for running task before disabling buttons to avoid stuck UI
        if self.current_worker is not None and self.current_worker.is_alive():
            from PySide6.QtWidgets import QMessageBox
            QMessageBox.warning(self, "Preview", "Another task is already running")
            return

        self.preview_start_btn.setEnabled(False)
        self.preview_stop_btn.setEnabled(True)

        def job() -> None:
            try:
                results, report = run_translate(
                    entries,
                    output,
                    options,
                    on_progress=lambda done, total: self.signals.progress.emit(min(done, total), total),
                )
                self._log(f"Preview translation done: {report.translated}/{len(entries)} translated, "
                          f"{report.fallback} fallback, {report.reused_memory} from memory")
                # Also emit final results for rows that might not have been caught by batch signals
                self.signals.result_batch.emit(results)
            except RuntimeError as exc:
                if str(exc) != "Stopped by user":
                    self.signals.error.emit("Preview", str(exc))
            except Exception as exc:
                self.signals.error.emit("Preview", str(exc))

        self._run("preview translate", job)  # type: ignore[attr-defined]

    def _preview_stop(self) -> None:
        self.stop_requested.set()
        self.preview_stop_btn.setEnabled(False)

    # ------------------------------------------------------------------
    # Live batch results from translation
    # ------------------------------------------------------------------

    def _preview_on_batch(self, results: list) -> None:
        for r in results:
            ident = text_identity(r.file, r.key)
            idx = self._preview_entry_to_row.get(ident)
            if idx is not None and idx < len(self._preview_rows):
                row = self._preview_rows[idx]
                if not row.manually_edited:
                    row.target = r.target
                    row.compute_warnings()
                    # Update the tree item if it exists in the current filter
                    self._preview_update_tree_item(row)
        self._preview_update_progress()

    # ------------------------------------------------------------------
    # Filter / search
    # ------------------------------------------------------------------

    def _preview_populate_contexts(self) -> None:
        contexts: set[str] = set()
        for row in self._preview_rows:
            hint = _context_hint(row.context) if row.context else ""
            if hint:
                contexts.add(hint)
        self.preview_context_combo.blockSignals(True)
        self.preview_context_combo.clear()
        self.preview_context_combo.addItem("All")
        for ctx in sorted(contexts):
            self.preview_context_combo.addItem(ctx)
        self.preview_context_combo.blockSignals(False)

    def _preview_apply_filter(self) -> None:
        self._preview_save_current(update_tree=False)

        mode = "all"
        for btn in self.preview_filter_group.buttons():
            if btn.isChecked():
                mode = btn.property("filter_value")
                break

        context_filter = self.preview_context_combo.currentText()
        search = self.preview_search_edit.text().lower().strip()

        self.preview_tree.clear()
        self._preview_filtered = []

        for row in self._preview_rows:
            if mode == "fallback" and row.status() not in (_PREVIEW_STATUS_FALLBACK, _PREVIEW_STATUS_PENDING):
                continue
            if mode == "warn" and not row.warnings:
                continue
            if context_filter != "All":
                hint = _context_hint(row.context) if row.context else ""
                if hint != context_filter:
                    continue
            if search and search not in (row.source + row.target + row.key + row.file).lower():
                continue

            self._preview_filtered.append(row.idx)
            self._preview_add_tree_item(row)

        self.preview_count_label.setText(f"Showing {len(self._preview_filtered)}/{len(self._preview_rows)}")

    def _preview_add_tree_item(self, row: PreviewRow) -> None:
        icon = _STATUS_ICONS.get(row.status(), " ")
        item = QTreeWidgetItem([
            str(row.idx + 1), row.file, row.key,
            _truncate(row.source, 80), _truncate(row.target, 80), icon,
        ])
        item.setData(0, Qt.ItemDataRole.UserRole, row.idx)
        self._preview_color_item(item, row)
        self.preview_tree.addTopLevelItem(item)

    def _preview_update_tree_item(self, row: PreviewRow) -> None:
        for i in range(self.preview_tree.topLevelItemCount()):
            item = self.preview_tree.topLevelItem(i)
            if item is not None and int(item.data(0, Qt.ItemDataRole.UserRole)) == row.idx:
                icon = _STATUS_ICONS.get(row.status(), " ")
                item.setText(4, _truncate(row.target, 80))
                item.setText(5, icon)
                self._preview_color_item(item, row)
                break

    def _preview_color_item(self, item: QTreeWidgetItem, row: PreviewRow) -> None:
        if row.status() == _PREVIEW_STATUS_FALLBACK or row.status() == _PREVIEW_STATUS_PENDING:
            color = QColor(204, 68, 0)
        elif row.status() == _PREVIEW_STATUS_WARN:
            color = QColor(180, 130, 0)
        else:
            color = self.preview_tree.palette().text().color()
        for c in range(6):
            item.setForeground(c, color)

    def _preview_confirm_bulk_replace(self) -> None:
        find = self.preview_bulk_find_edit.text()
        if not find:
            from PySide6.QtWidgets import QMessageBox
            QMessageBox.warning(self, "Bulk replace", "Enter text to find.")
            return
        matches = self._preview_count_bulk_replace_matches()
        if matches == 0:
            from PySide6.QtWidgets import QMessageBox
            QMessageBox.information(self, "Bulk replace", "No matches in filtered rows.")
            return
        from PySide6.QtWidgets import QMessageBox
        reply = QMessageBox.question(
            self,
            "Bulk replace",
            f"Replace {matches} target row(s) in the current filtered list?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return
        changed = self._preview_apply_bulk_replace()
        QMessageBox.information(self, "Bulk replace", f"Updated {changed} row(s).")

    def _preview_count_bulk_replace_matches(self) -> int:
        find = self.preview_bulk_find_edit.text()
        if not find:
            return 0
        case_sensitive = self.preview_bulk_case_check.isChecked()
        needle = find if case_sensitive else find.lower()
        count = 0
        for idx in self._preview_filtered:
            row = self._preview_rows[idx]
            haystack = row.target if case_sensitive else row.target.lower()
            if needle in haystack:
                count += 1
        return count

    def _preview_apply_bulk_replace(self) -> int:
        self._preview_save_current(update_tree=True)
        find = self.preview_bulk_find_edit.text()
        if not find:
            return 0
        replace = self.preview_bulk_replace_edit.text()
        case_sensitive = self.preview_bulk_case_check.isChecked()
        changed = 0
        for idx in self._preview_filtered:
            row = self._preview_rows[idx]
            if case_sensitive:
                new_target = row.target.replace(find, replace)
            else:
                new_target = _replace_case_insensitive(row.target, find, replace)
            if new_target == row.target:
                continue
            row.target = new_target
            row.manually_edited = True
            row.compute_warnings()
            self._preview_dirty = True
            changed += 1
            self._preview_update_tree_item(row)
        if self._preview_current_idx is not None:
            current = self._preview_rows[self._preview_current_idx]
            self.preview_target_box.setPlainText(current.target)
            self.preview_warning_label.setText("\n".join(current.warnings) if current.warnings else "")
        self._preview_update_progress()
        return changed

    # ------------------------------------------------------------------
    # Selection / editing
    # ------------------------------------------------------------------

    def _preview_on_select(self) -> None:
        items = self.preview_tree.selectedItems()
        if not items:
            return
        self._preview_save_current(update_tree=True)
        idx = int(items[0].data(0, Qt.ItemDataRole.UserRole))
        self._preview_current_idx = idx
        row = self._preview_rows[idx]
        self.preview_source_box.setPlainText(row.source)
        self.preview_target_box.setPlainText(row.target)
        self.preview_warning_label.setText("\n".join(row.warnings) if row.warnings else "")

    def _preview_save_current(self, update_tree: bool = True) -> None:
        if self._preview_current_idx is None:
            return
        row = self._preview_rows[self._preview_current_idx]
        new_target = self.preview_target_box.toPlainText().rstrip("\n")
        if new_target != row.target:
            row.target = new_target
            row.manually_edited = True
            row.compute_warnings()
            self._preview_dirty = True
            if update_tree:
                self._preview_update_tree_item(row)
                self.preview_warning_label.setText("\n".join(row.warnings) if row.warnings else "")

    def _preview_copy_source(self) -> None:
        self.preview_target_box.setPlainText(self.preview_source_box.toPlainText())
        self._preview_save_current(update_tree=True)

    def _preview_next_warning(self) -> None:
        if not self._preview_rows:
            return
        start = (self._preview_current_idx or 0) + 1
        for i in range(start, len(self._preview_rows)):
            if self._preview_rows[i].warnings:
                self._preview_select_row(i)
                return
        for i in range(0, start):
            if self._preview_rows[i].warnings:
                self._preview_select_row(i)
                return

    def _preview_select_row(self, idx: int) -> None:
        for i in range(self.preview_tree.topLevelItemCount()):
            item = self.preview_tree.topLevelItem(i)
            if item is not None and int(item.data(0, Qt.ItemDataRole.UserRole)) == idx:
                self.preview_tree.setCurrentItem(item)
                self.preview_tree.scrollToItem(item)
                return

    # ------------------------------------------------------------------
    # Save CSV
    # ------------------------------------------------------------------

    def _preview_save(self) -> None:
        self._preview_save_current(update_tree=False)
        output_path = self.preview_output_edit.text().strip()
        if not output_path:
            from PySide6.QtWidgets import QMessageBox
            QMessageBox.warning(self, "Save CSV", "No output path specified.")
            return
        path = Path(output_path)
        results = [
            TranslationResult(
                file=Path(r.file), key=r.key, source=r.source,
                target=r.target, context=r.context, sub_keys=r.sub_keys,
            )
            for r in self._preview_rows
        ]
        try:
            save_results(results, path)
            self._preview_dirty = False
            from PySide6.QtWidgets import QMessageBox
            QMessageBox.information(self, "Save CSV", f"Saved {len(results)} rows to {path}")
        except Exception as exc:
            from PySide6.QtWidgets import QMessageBox
            QMessageBox.warning(self, "Save CSV", f"Failed to save: {exc}")

    # ------------------------------------------------------------------
    # Progress helpers
    # ------------------------------------------------------------------

    def _preview_update_progress(self) -> None:
        total = len(self._preview_rows)
        done = sum(1 for r in self._preview_rows if r.target.strip() and r.target != r.source)
        warns = sum(1 for r in self._preview_rows if r.warnings)
        self.preview_progress_label.setText(f"Translated: {done}/{total}  |  Warnings: {warns}")


def _replace_case_insensitive(text: str, find: str, replace: str) -> str:
    if not find:
        return text
    return re.sub(re.escape(find), lambda _match: replace, text, flags=re.IGNORECASE)


def _truncate(s: str, n: int) -> str:
    if len(s) <= n:
        return s
    return s[: n - 1] + "…"
