from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ..signals import WorkerSignals

from pathlib import Path
from typing import Callable, cast

from PySide6.QtWidgets import (
    QComboBox,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from ...auto import analyze_game, write_analysis_report
from ...rpg_maker import engine_to_gui_game_type, normalize_gui_game_type


class GameTabMixin:
    signals: WorkerSignals
    config: dict
    game_dir_edit: QLineEdit
    game_type_combo: QComboBox
    texts_csv_edit: QLineEdit
    translations_csv_edit: QLineEdit
    out_dir_edit: QLineEdit
    scan_summary_label: QLabel
    source_lang_edit: QLineEdit
    target_lang_edit: QLineEdit
    provider_combo: QComboBox
    _log: Callable
    _check_stopped: Callable
    _run: Callable
    _set_default_work_paths: Callable
    _game_dir_path: Callable
    _path_picker: Callable
    _action_button: Callable

    # ----- Game tab -----

    def _build_game_tab(self) -> QWidget:
        tab = QWidget()
        outer = QVBoxLayout(tab)
        form = QFormLayout()
        outer.addLayout(form)

        self.game_dir_edit = QLineEdit(str(self.config.get("game_dir", "")))
        form.addRow("Game folder", self._path_picker(self.game_dir_edit, self._choose_game_dir))

        self.game_type_combo = QComboBox()
        self.game_type_combo.addItems(["rpg-maker-mv", "rpg-maker-mz", "unity-xunity"])
        self.game_type_combo.setCurrentText(normalize_gui_game_type(str(self.config.get("game_type", "rpg-maker-mv"))))
        form.addRow("Game type", self.game_type_combo)

        self.texts_csv_edit = QLineEdit(str(self.config.get("texts_csv", "work/texts.csv")))
        self.texts_csv_edit.setReadOnly(True)
        form.addRow("Texts CSV", self.texts_csv_edit)

        self.translations_csv_edit = QLineEdit(str(self.config.get("translations_csv", "work/translations.csv")))
        self.translations_csv_edit.setReadOnly(True)
        form.addRow("Translations CSV", self.translations_csv_edit)

        self.out_dir_edit = QLineEdit(str(self.config.get("out_dir", "work/translated_data")))
        self.out_dir_edit.setReadOnly(True)
        form.addRow("Output folder", self.out_dir_edit)

        # Action row
        action_row = QHBoxLayout()
        action_row.addWidget(self._action_button("Scan Game", self.scan))
        action_row.addStretch()
        outer.addLayout(action_row)

        self.scan_summary_label = QLabel("Choose a game folder, then scan.")
        self.scan_summary_label.setWordWrap(True)
        outer.addWidget(self.scan_summary_label)

        outer.addStretch()
        return tab

    def _choose_game_dir(self) -> None:
        from PySide6.QtWidgets import QFileDialog
        value = QFileDialog.getExistingDirectory(self, "Select game folder", self.game_dir_edit.text())
        if value:
            self.game_dir_edit.setText(value)
            self._set_default_work_paths(Path(value))
            self.refresh_backups()
            self.refresh_cheat_status()
            self.refresh_xunity_status()

    def scan(self) -> None:
        # Read widget values on the main thread before starting the worker
        provider = self.provider_combo.currentText()
        target_lang = self.target_lang_edit.text()

        def job() -> None:
            game_dir = self._game_dir_path()
            self.signals.set_text.emit("scan_summary", "Scanning...")
            report = analyze_game(game_dir, provider, target_lang)
            self._set_default_work_paths(game_dir, use_signals=True)
            if report["engine"] in {"mv", "mz", "mv-mz"}:
                self.signals.set_text.emit("game_type", engine_to_gui_game_type(str(report["engine"])))
            elif report["engine"] == "unity-xunity":
                self.signals.set_text.emit("game_type", "unity-xunity")
            write_analysis_report(report, game_dir / "translator_work" / "analysis.json")
            summary = f"Engine: {report['engine']} | JSON files: {report['json_files']} | Text entries: {report['text_entries']} | Data folder: {report['data_dir']}"
            if report.get("unsupported_reason"):
                summary += f" | NOT SUPPORTED: {report['unsupported_reason']}"
            self.signals.set_text.emit("scan_summary", summary)
            self._log(summary)
            for warning in cast(list[str], report.get("extract_warnings", [])):
                self._log(f"WARN: {warning}")
            self.signals.refresh_backups.emit()
            self.signals.refresh_cheat.emit()
            self.signals.refresh_xunity.emit()
        self._run("scan game", job)