from __future__ import annotations

import csv
import shutil
import sys
import threading
import time
import concurrent.futures
from pathlib import Path
from typing import Any, Callable, cast

from PySide6.QtCore import QObject, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QFont, QIcon, QPalette
from PySide6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QButtonGroup,
    QCheckBox,
    QComboBox,
    QDialog,
    QFileDialog,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QPlainTextEdit,
    QProgressBar,
    QPushButton,
    QRadioButton,
    QSpinBox,
    QTabWidget,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from .app_config import load_app_config, save_app_config
from .app_logging import is_api_logging_enabled, log_event, logs_dir, set_api_logging
from .auto import analyze_game, auto_translate_game, write_analysis_report
from .csv_store import load_entries, load_results, save_results
from .editor import open_file_editor
from .translate_pipeline import TranslateOptions, run_translate
from .translation_memory import global_memory_path
from .models import TextEntry, TranslationResult, text_identity
from .rpg_maker import (
    apply_rpg_maker,
    engine_to_gui_game_type,
    extract_rpg_maker_mv,
    extract_rpg_maker_mz,
    normalize_gui_game_type,
)
from .unity_setup import detect_unity_bare, install_xunity, uninstall_xunity, xunity_install_status, xunity_manifest_path, resolve_xunity_lang, _write_xunity_config
from .rpg_maker_cheat import (
    apply_cheat,
    cheat_manifest_path,
    cheat_status,
    detect_cheat_engine,
    remove_cheat,
)
from .path_utils import timestamped_unique_path
from .xunity import apply_xunity, detect_xunity, extract_xunity


class WorkerSignals(QObject):
    log = Signal(str, str)          # level, message
    log_detail = Signal(str, str, str)  # level, source, message
    progress = Signal(int, int)
    progress_text = Signal(str)
    status = Signal(str)
    finished = Signal()
    error = Signal(str, str)
    info = Signal(str, str)
    refresh_backups = Signal()
    refresh_cheat = Signal()
    refresh_xunity = Signal()
    set_text = Signal(str, str)
    set_checked = Signal(str, bool)


def _resource_dir(name: str) -> Path:
    bundle_dir = getattr(sys, "_MEIPASS", None)
    if bundle_dir:
        return Path(bundle_dir) / name
    return Path(__file__).resolve().parent.parent.parent / name


class TranslatorGUI(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("Game LLM Translator")
        self.resize(1060, 780)
        self.setMinimumSize(980, 680)
        self._set_window_icon()

        self.config = load_app_config()
        self.stop_requested = threading.Event()
        self.current_worker: threading.Thread | None = None
        self.action_buttons: list[QPushButton] = []
        self.safe_buttons: list[QPushButton] = []
        self.backup_paths: list[Path] = []
        self.translate_progress: dict[str, float | int] = {"done": 0, "total": 0, "started": 0.0}

        self.signals = WorkerSignals()
        self.signals.log.connect(self._append_log)
        self.signals.log_detail.connect(self._append_detail_log)
        self._log_row_limit = 2000
        self._detail_row_limit = 5000
        self.signals.progress.connect(self._on_progress)
        self.signals.progress_text.connect(self._on_progress_text)
        self.signals.status.connect(self._on_status)
        self.signals.finished.connect(self._on_finished)
        self.signals.error.connect(self._on_error)
        self.signals.info.connect(self._on_info)
        self.signals.refresh_backups.connect(self.refresh_backups)
        self.signals.refresh_cheat.connect(self.refresh_cheat_status)
        self.signals.refresh_xunity.connect(self.refresh_xunity_status)
        self.signals.set_text.connect(self._on_set_text)
        self.signals.set_checked.connect(self._on_set_checked)

        self._build_ui()
        self._apply_theme(self.config.get("theme", "light"))
        self._update_api_fields()
        self.refresh_backups()
        self.refresh_cheat_status()
        self.refresh_xunity_status()

    def _set_window_icon(self) -> None:
        try:
            asset_dir = _resource_dir("assets")
            ico = asset_dir / "icon.ico"
            png = asset_dir / "icon.png"
            if sys.platform == "win32" and ico.exists():
                self.setWindowIcon(QIcon(str(ico)))
            elif png.exists():
                self.setWindowIcon(QIcon(str(png)))
        except Exception:
            pass

    def _apply_theme(self, mode: str) -> None:
        app = QApplication.instance()
        if app is None:
            return
        qt_app = cast(QApplication, app)
        if mode == "dark":
            role = QPalette.ColorRole
            palette = QPalette()
            palette.setColor(role.Window, QColor(45, 45, 48))
            palette.setColor(role.WindowText, QColor(220, 220, 220))
            palette.setColor(role.Base, QColor(30, 30, 30))
            palette.setColor(role.AlternateBase, QColor(45, 45, 48))
            palette.setColor(role.Text, QColor(220, 220, 220))
            palette.setColor(role.Button, QColor(60, 60, 65))
            palette.setColor(role.ButtonText, QColor(220, 220, 220))
            palette.setColor(role.Highlight, QColor(91, 108, 255))
            palette.setColor(role.HighlightedText, QColor(255, 255, 255))
            palette.setColor(role.ToolTipBase, QColor(45, 45, 48))
            palette.setColor(role.ToolTipText, QColor(220, 220, 220))
            qt_app.setPalette(palette)
            self.theme_mode = "dark"
        else:
            qt_app.setPalette(qt_app.style().standardPalette())
            self.theme_mode = "light"

    def _toggle_theme(self) -> None:
        new_mode = "dark" if self.theme_mode == "light" else "light"
        self._apply_theme(new_mode)
        cfg = load_app_config()
        cfg["theme"] = new_mode
        save_app_config(cfg)

    # ------------------------------------------------------------------
    # UI construction
    # ------------------------------------------------------------------

    def _build_ui(self) -> None:
        central = QWidget()
        self.setCentralWidget(central)
        root = QVBoxLayout(central)
        root.setContentsMargins(12, 12, 12, 12)

        # Header
        header = QHBoxLayout()
        title = QLabel("Game LLM Translator")
        font = title.font()
        font.setPointSize(14)
        font.setBold(True)
        title.setFont(font)
        header.addWidget(title)
        header.addStretch()
        self.theme_button = QPushButton("Toggle theme")
        self.theme_button.clicked.connect(self._toggle_theme)
        header.addWidget(self.theme_button)
        root.addLayout(header)

        # Tabs
        self.tabs = QTabWidget()
        self.tabs.addTab(self._build_game_tab(), "Game")
        self.tabs.addTab(self._build_provider_tab(), "Provider")
        self.tabs.addTab(self._build_translate_tab(), "Translate")
        self.tabs.addTab(self._build_review_tab(), "Review")
        self.tabs.addTab(self._build_apply_tab(), "Apply")
        self.tabs.addTab(self._build_recovery_tab(), "Backups")
        self.tabs.addTab(self._build_activity_tab(), "Activity")
        self.tabs.addTab(self._build_detail_tab(), "Detail")
        self.tabs.addTab(self._build_api_log_tab(), "API Log")
        root.addWidget(self.tabs, 1)

        # Status bar
        bar = self.statusBar()
        self.status_label = QLabel("Idle")
        bar.addWidget(self.status_label)
        self.progress_bar = QProgressBar()
        self.progress_bar.setMaximumWidth(260)
        self.progress_bar.setRange(0, 0)  # indeterminate
        self.progress_bar.setVisible(False)
        bar.addPermanentWidget(self.progress_bar)
        self.progress_text_label = QLabel("")
        bar.addPermanentWidget(self.progress_text_label)
        self.stop_button = QPushButton("Stop")
        self.stop_button.setEnabled(False)
        self.stop_button.clicked.connect(self.stop_current)
        bar.addPermanentWidget(self.stop_button)

    # ----- helper widgets -----

    def _add_form_row(self, layout: QFormLayout, label: str, widget: QWidget) -> None:
        layout.addRow(label, widget)

    def _path_picker(self, var: QLineEdit, on_browse: Callable[[], None]) -> QWidget:
        wrap = QWidget()
        h = QHBoxLayout(wrap)
        h.setContentsMargins(0, 0, 0, 0)
        h.addWidget(var, 1)
        btn = QPushButton("Browse")
        btn.clicked.connect(on_browse)
        h.addWidget(btn)
        return wrap

    def _action_button(self, text: str, slot: Callable[[], None]) -> QPushButton:
        btn = QPushButton(text)
        btn.clicked.connect(slot)
        self.action_buttons.append(btn)
        return btn

    def _safe_button(self, text: str, slot: Callable[[], None]) -> QPushButton:
        """Button that stays enabled even while a task is running (view/log only)."""
        btn = QPushButton(text)
        btn.clicked.connect(slot)
        self.safe_buttons.append(btn)
        return btn

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

    # ----- Provider tab -----

    def _build_provider_tab(self) -> QWidget:
        tab = QWidget()
        form = QFormLayout(tab)

        self.provider_combo = QComboBox()
        self.provider_combo.addItems(["google", "mymemory", "libretranslate", "bing", "yandex", "anthropic", "anthropic-compatible", "openai", "openai-compatible"])
        self.provider_combo.setCurrentText(str(self.config.get("provider", "google")))
        self.provider_combo.currentTextChanged.connect(lambda _: self._update_api_fields())
        form.addRow("Provider", self.provider_combo)

        self.model_edit = QLineEdit(str(self.config.get("model", "google")))
        form.addRow("Model", self.model_edit)

        self.api_key_edit = QLineEdit(str(self.config.get("api_key", "")))
        self.api_key_edit.setEchoMode(QLineEdit.EchoMode.Password)
        form.addRow("API key", self.api_key_edit)

        self.api_base_edit = QLineEdit(str(self.config.get("api_base", "")))
        form.addRow("Base URL", self.api_base_edit)

        self.source_lang_edit = QLineEdit(str(self.config.get("source_lang", "auto")))
        form.addRow("Source language", self.source_lang_edit)

        self.target_lang_edit = QLineEdit(str(self.config.get("target_lang", "Vietnamese")))
        form.addRow("Target language", self.target_lang_edit)

        self.remember_api_check = QCheckBox("Remember API key/base on this machine")
        self.remember_api_check.setChecked(bool(self.config.get("remember_api_key", bool(self.config.get("api_key")))))
        form.addRow("", self.remember_api_check)

        self.log_api_check = QCheckBox("Log full API requests & responses (api-debug.log) — debug only, may contain sensitive data")
        self.log_api_check.setChecked(bool(self.config.get("log_api_calls", False)))
        self.log_api_check.toggled.connect(self._on_api_logging_toggled)
        set_api_logging(self.log_api_check.isChecked())
        form.addRow("", self.log_api_check)

        self.provider_note = QLabel("")
        self.provider_note.setWordWrap(True)
        self.provider_note.setStyleSheet("color: #666;")
        form.addRow("", self.provider_note)

        save_btn = QPushButton("Save provider settings")
        save_btn.clicked.connect(self.save_settings)
        form.addRow("", save_btn)

        open_api_log_btn = self._safe_button("Open API Debug Log", self._open_api_log)
        form.addRow("", open_api_log_btn)

        return tab

    def _update_api_fields(self) -> None:
        provider = self.provider_combo.currentText()
        notes = {
            "google": "No API key required. MTL output may be rough; review translations before applying.",
            "mymemory": "No API key required. Public service may rate-limit requests.",
            "libretranslate": "Set LibreTranslate URL/API key here if using a custom server; otherwise environment defaults may be used.",
            "bing": "Microsoft Translator may require key/region depending on your account setup.",
            "yandex": "Yandex may require API key and folder/project configuration.",
            "anthropic": "Uses Anthropic Claude. Keep API keys private.",
            "openai": "Uses OpenAI chat completions. Keep API keys private.",
            "openai-compatible": "OpenRouter / LM Studio / Ollama / proxy. Set Base URL.",
            "anthropic-compatible": "Custom Anthropic Messages API endpoint (AWS Bedrock gateway, Vertex AI Claude proxy, etc). Set Base URL and API Key.",
        }
        self.provider_note.setText(notes.get(provider, ""))

    # ----- Translate tab -----

    def _build_translate_tab(self) -> QWidget:
        tab = QWidget()
        outer = QVBoxLayout(tab)

        actions = QHBoxLayout()
        actions.addWidget(self._action_button("Auto-translate (extract + translate + export)", self.auto_translate))
        actions.addWidget(self._action_button("Extract + Translate + Export Copy", self.pipeline))
        actions.addStretch()
        outer.addLayout(actions)

        advanced = QGroupBox("Advanced translation options")
        adv = QFormLayout(advanced)

        self.batch_size_spin = QSpinBox()
        self.batch_size_spin.setRange(0, 200)
        self.batch_size_spin.setValue(int(self.config.get("batch_size", 30)))
        adv.addRow("Batch size (0 = auto)", self.batch_size_spin)

        self.workers_spin = QSpinBox()
        self.workers_spin.setRange(1, 8)
        self.workers_spin.setValue(int(self.config.get("workers", 1)))
        adv.addRow("Workers (parallel)", self.workers_spin)

        self.glossary_path_edit = QLineEdit(str(self.config.get("glossary_path", "")))
        adv.addRow("Glossary CSV (optional)", self._path_picker(self.glossary_path_edit, self._choose_glossary))

        self.correction_table_path_edit = QLineEdit(str(self.config.get("correction_table_path", "")))
        adv.addRow("Correction table CSV (optional)", self._path_picker(self.correction_table_path_edit, self._choose_correction_table))

        self.restart_check = QCheckBox("Ignore existing translations and start over")
        adv.addRow("", self.restart_check)

        self.reuse_memory_check = QCheckBox("Reuse translation memory")
        self.reuse_memory_check.setChecked(bool(self.config.get("reuse_memory", True)))
        adv.addRow("", self.reuse_memory_check)

        self.save_memory_check = QCheckBox("Save successful translations to memory")
        self.save_memory_check.setChecked(bool(self.config.get("save_memory", True)))
        adv.addRow("", self.save_memory_check)

        hint = QLabel(
            "Glossary CSV columns: term, translation, [note]. Terms are injected into every LLM prompt.\n"
            "Correction table CSV columns: find, replace. Applied as post-processing after each batch."
        )
        hint.setWordWrap(True)
        hint.setStyleSheet("color: #555;")
        adv.addRow("", hint)

        outer.addWidget(advanced)
        outer.addStretch()
        return tab

    # ----- Review tab -----

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

    # ----- Apply tab -----

    def _build_apply_tab(self) -> QWidget:
        tab = QWidget()
        outer = QVBoxLayout(tab)

        export = QGroupBox("Export translated data")
        e = QHBoxLayout(export)
        e.addWidget(self._action_button("Export Translated Data", self.export_translated_data))
        e.addStretch()
        outer.addWidget(export)

        risky = QGroupBox("Apply translated data to game")
        r = QVBoxLayout(risky)
        r_label = QLabel("Creates a backup, then replaces JSON files in the game data folder. Close the game first.")
        r_label.setWordWrap(True)
        r_label.setStyleSheet("color: #8a0000;")
        r.addWidget(r_label)
        r_row = QHBoxLayout()
        r_row.addWidget(self._action_button("Apply to Game...", self.apply_to_game))
        r_row.addStretch()
        r.addLayout(r_row)
        outer.addWidget(risky)

        xunity = QGroupBox("BepInEx + XUnity.AutoTranslator (Unity games)")
        xu = QVBoxLayout(xunity)
        xu_label = QLabel(
            "Automatically installs BepInEx + XUnity.AutoTranslator into a Unity game.\n"
            "After installing: run the game once so XUnity collects text, then scan again."
        )
        xu_label.setWordWrap(True)
        xu.addWidget(xu_label)
        self.xunity_status_label = QLabel("XUnity: no game selected")
        self.xunity_status_label.setWordWrap(True)
        self.xunity_status_label.setStyleSheet("color: #555;")
        xu.addWidget(self.xunity_status_label)
        xu_row = QHBoxLayout()
        xu_row.addWidget(self._action_button("Install BepInEx + XUnity...", self.install_xunity_plugin))
        xu_row.addWidget(self._action_button("Fix Config / Language", self.fix_xunity_config))
        xu_row.addWidget(self._action_button("Uninstall XUnity", self.uninstall_xunity_plugin))
        xu_row.addWidget(self._action_button("Refresh", self.refresh_xunity_status))
        xu_row.addStretch()
        xu.addLayout(xu_row)
        outer.addWidget(xunity)

        cheat = QGroupBox("Cheat plugin (RPG Maker MV/MZ)")
        c = QVBoxLayout(cheat)
        c_label = QLabel("Installs RPG Maker MV/MZ Cheat UI Plugin. Toggle in game: Ctrl+C. Remove uses this app's manifest only.")
        c_label.setWordWrap(True)
        c.addWidget(c_label)
        self.cheat_status_label = QLabel("Cheat plugin: no game selected")
        self.cheat_status_label.setWordWrap(True)
        self.cheat_status_label.setStyleSheet("color: #555;")
        c.addWidget(self.cheat_status_label)
        c_row = QHBoxLayout()
        c_row.addWidget(self._action_button("Apply Cheat...", self.apply_cheat_plugin))
        c_row.addWidget(self._action_button("Remove Cheat", self.remove_cheat_plugin))
        c_row.addWidget(self._action_button("Refresh Status", self.refresh_cheat_status))
        c_row.addStretch()
        c.addLayout(c_row)
        outer.addWidget(cheat)

        outer.addStretch()
        return tab

    # ----- Recovery / Backups tab -----

    def _build_recovery_tab(self) -> QWidget:
        tab = QWidget()
        outer = QVBoxLayout(tab)

        backup_group = QGroupBox("Backup actions")
        b = QHBoxLayout(backup_group)
        b.addWidget(self._safe_button("Refresh", self.refresh_backups))
        b.addWidget(self._action_button("Create Backup", self.create_backup_now))
        b.addWidget(self._safe_button("Open Selected", self.open_selected_backup))
        b.addWidget(self._action_button("Restore Selected", self.restore_backup))
        b.addWidget(self._action_button("Delete Selected", self.delete_selected_backups))
        b.addWidget(self._action_button("Delete All", self.delete_all_backups))
        b.addStretch()
        outer.addWidget(backup_group)

        memory_group = QGroupBox("Memory & translation cleanup")
        m = QHBoxLayout(memory_group)
        m.addWidget(self._action_button("Clear Old Translation", self.clear_old_translation))
        m.addWidget(self._action_button("Clear Game Memory", self.clear_game_memory))
        m.addWidget(self._action_button("Clear Global Memory", self.clear_global_memory))
        m.addStretch()
        outer.addWidget(memory_group)

        self.backups_tree = QTreeWidget()
        self.backups_tree.setHeaderLabels(["Backup type", "Path"])
        self.backups_tree.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.backups_tree.header().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        outer.addWidget(self.backups_tree, 1)

        info = QLabel("Restore creates data_before_restore_* first. Delete only removes listed backup folders; game data is not changed.")
        info.setWordWrap(True)
        info.setStyleSheet("color: #555;")
        outer.addWidget(info)
        return tab

    # ----- Activity tab & Detail tab -----

    def _make_log_table(self, columns: list[str]) -> QTreeWidget:
        t = QTreeWidget()
        t.setHeaderLabels(columns)
        t.setRootIsDecorated(False)
        t.setAlternatingRowColors(True)
        t.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        t.setSortingEnabled(False)
        t.header().setStretchLastSection(True)
        mono = QFont("Consolas", 8)
        t.setFont(mono)
        return t

    def _build_activity_tab(self) -> QWidget:
        tab = QWidget()
        outer = QVBoxLayout(tab)
        toolbar = QHBoxLayout()
        toolbar.addWidget(self._safe_button("Open Log Folder", self.open_logs))
        clear_btn = QPushButton("Clear")
        clear_btn.clicked.connect(lambda: self.log_view.clear())
        toolbar.addWidget(clear_btn)
        toolbar.addStretch()
        outer.addLayout(toolbar)
        self.log_view = self._make_log_table(["Time", "Level", "Message"])
        self.log_view.setColumnWidth(0, 80)
        self.log_view.setColumnWidth(1, 55)
        outer.addWidget(self.log_view, 1)
        return tab

    def _build_detail_tab(self) -> QWidget:
        tab = QWidget()
        outer = QVBoxLayout(tab)
        toolbar = QHBoxLayout()
        toolbar.addWidget(self._safe_button("Open Log Folder", self.open_logs))
        clear_btn = QPushButton("Clear")
        clear_btn.clicked.connect(lambda: self.detail_log_view.clear())
        toolbar.addWidget(clear_btn)
        toolbar.addStretch()
        outer.addLayout(toolbar)
        self.detail_log_view = self._make_log_table(["Time", "Level", "Source", "Message"])
        self.detail_log_view.setColumnWidth(0, 80)
        self.detail_log_view.setColumnWidth(1, 55)
        self.detail_log_view.setColumnWidth(2, 120)
        outer.addWidget(self.detail_log_view, 1)
        return tab

    def _build_api_log_tab(self) -> QWidget:
        tab = QWidget()
        outer = QVBoxLayout(tab)

        toolbar = QHBoxLayout()
        refresh_btn = self._safe_button("Refresh", self._api_log_refresh)
        toolbar.addWidget(refresh_btn)
        clear_btn = QPushButton("Clear view")
        clear_btn.clicked.connect(lambda: self.api_log_view.clear())
        toolbar.addWidget(clear_btn)
        toolbar.addWidget(self._safe_button("Open File", self._open_api_log))
        toolbar.addWidget(self._safe_button("Open Log Folder", self.open_logs))

        self._api_log_follow = QCheckBox("Follow (auto-scroll)")
        self._api_log_follow.setChecked(True)
        toolbar.addWidget(self._api_log_follow)
        toolbar.addStretch()

        hint = QLabel("Enable 'Log full API requests & responses' in Provider tab to populate this log.")
        hint.setStyleSheet("color: #888; font-size: 11px;")
        toolbar.addWidget(hint)

        outer.addLayout(toolbar)

        self.api_log_view = QPlainTextEdit()
        self.api_log_view.setReadOnly(True)
        self.api_log_view.setFont(QFont("Consolas", 8))
        outer.addWidget(self.api_log_view, 1)

        self._api_log_file_size = 0
        self._api_log_timer = QTimer(self)
        self._api_log_timer.setInterval(2000)
        self._api_log_timer.timeout.connect(self._api_log_poll)

        self.tabs.currentChanged.connect(self._on_tab_changed)
        return tab

    def _on_tab_changed(self, index: int) -> None:
        api_log_index = self.tabs.count() - 1
        if index == api_log_index:
            self._api_log_refresh()
            self._api_log_timer.start()
        else:
            self._api_log_timer.stop()

    def _api_log_poll(self) -> None:
        from .app_logging import api_log_file_path
        path = api_log_file_path()
        if not path.exists():
            return
        size = path.stat().st_size
        if size != self._api_log_file_size:
            self._api_log_refresh()

    def _api_log_refresh(self) -> None:
        from .app_logging import api_log_file_path
        path = api_log_file_path()
        if not path.exists():
            self.api_log_view.setPlaceholderText(f"No API debug log yet today.\nFile: {path}")
            return
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
            self._api_log_file_size = path.stat().st_size
            self.api_log_view.setPlainText(text)
            if self._api_log_follow.isChecked():
                sb = self.api_log_view.verticalScrollBar()
                sb.setValue(sb.maximum())
        except Exception:
            pass

    # ------------------------------------------------------------------
    # Helpers / state
    # ------------------------------------------------------------------

    def _choose_game_dir(self) -> None:
        value = QFileDialog.getExistingDirectory(self, "Select game folder", self.game_dir_edit.text())
        if value:
            self.game_dir_edit.setText(value)
            self._set_default_work_paths(Path(value))
            self.refresh_backups()
            self.refresh_cheat_status()
            self.refresh_xunity_status()

    def _choose_glossary(self) -> None:
        value, _ = QFileDialog.getOpenFileName(self, "Select glossary CSV", self.glossary_path_edit.text(), "CSV files (*.csv);;All files (*)")
        if value:
            self.glossary_path_edit.setText(value)

    def _choose_correction_table(self) -> None:
        value, _ = QFileDialog.getOpenFileName(self, "Select correction table CSV", self.correction_table_path_edit.text(), "CSV files (*.csv);;All files (*)")
        if value:
            self.correction_table_path_edit.setText(value)

    def _set_default_work_paths(self, game_dir: Path, use_signals: bool = False) -> None:
        work = game_dir / "translator_work"
        texts = str(work / "texts.csv")
        translations = str(work / "translations.csv")
        out = str(work / "translated_data")
        if use_signals:
            self.signals.set_text.emit("texts_csv", texts)
            self.signals.set_text.emit("translations_csv", translations)
            self.signals.set_text.emit("out_dir", out)
        else:
            self.texts_csv_edit.setText(texts)
            self.translations_csv_edit.setText(translations)
            self.out_dir_edit.setText(out)

    def _game_dir_path(self) -> Path:
        value = self.game_dir_edit.text().strip()
        if not value:
            raise ValueError("Game folder not selected")
        path = Path(value)
        if not path.exists():
            raise ValueError(f"Game folder not found: {path}")
        return path

    def _game_data_dir(self, game_dir: Path) -> Path:
        for c in (game_dir / "www" / "data", game_dir / "data"):
            if c.exists():
                return c
        raise ValueError(f"No data folder found in {game_dir}")

    def save_settings(self) -> dict:
        data = {
            "game_type": self.game_type_combo.currentText(),
            "game_dir": self.game_dir_edit.text(),
            "texts_csv": self.texts_csv_edit.text(),
            "translations_csv": self.translations_csv_edit.text(),
            "out_dir": self.out_dir_edit.text(),
            "provider": self.provider_combo.currentText(),
            "model": self.model_edit.text(),
            "source_lang": self.source_lang_edit.text(),
            "target_lang": self.target_lang_edit.text(),
            "batch_size": int(self.batch_size_spin.value()),
            "workers": int(self.workers_spin.value()),
            "glossary_path": self.glossary_path_edit.text(),
            "correction_table_path": self.correction_table_path_edit.text(),
            "theme": getattr(self, "theme_mode", "light"),
            "remember_api_key": self.remember_api_check.isChecked(),
            "log_api_calls": self.log_api_check.isChecked(),
            "reuse_memory": self.reuse_memory_check.isChecked(),
            "save_memory": self.save_memory_check.isChecked(),
        }
        if self.remember_api_check.isChecked():
            data["api_key"] = self.api_key_edit.text()
            data["api_base"] = self.api_base_edit.text()
        save_app_config(data)
        self._log("Settings saved. API key/base are stored as plaintext on this machine.")
        return data

    # ------------------------------------------------------------------
    # Logging / status updates (signals)
    # ------------------------------------------------------------------

    _LEVEL_COLORS = {
        "ERROR": QColor(200, 50, 50),
        "WARN":  QColor(200, 130, 0),
        "DEBUG": QColor(130, 130, 130),
    }

    def _append_log(self, level: str, msg: str) -> None:
        from datetime import datetime
        ts = datetime.now().strftime("%H:%M:%S")
        item = QTreeWidgetItem([ts, level, msg])
        if level in self._LEVEL_COLORS:
            c = self._LEVEL_COLORS[level]
            for col in range(3):
                item.setForeground(col, c)
        self.log_view.addTopLevelItem(item)
        self.log_view.scrollToItem(item)
        if self.log_view.topLevelItemCount() > self._log_row_limit:
            self.log_view.takeTopLevelItem(0)

    def _append_detail_log(self, level: str, source: str, msg: str) -> None:
        from datetime import datetime
        ts = datetime.now().strftime("%H:%M:%S.%f")[:-3]
        item = QTreeWidgetItem([ts, level, source, msg])
        if level in self._LEVEL_COLORS:
            c = self._LEVEL_COLORS[level]
            for col in range(4):
                item.setForeground(col, c)
        self.detail_log_view.addTopLevelItem(item)
        self.detail_log_view.scrollToItem(item)
        if self.detail_log_view.topLevelItemCount() > self._detail_row_limit:
            self.detail_log_view.takeTopLevelItem(0)

    _DETAIL_PREFIXES = (
        "Batch ", "Quality warning", "Saved ", "Pre-dedup:",
        "Batch failed", "Retrying", "retry_after", "Translated ",
    )

    @staticmethod
    def _infer_source(msg: str) -> str:
        """Best-effort: extract a short source label from the log message."""
        if msg.startswith("Batch failed"):
            return "pipeline"
        if msg.startswith("Quality warning"):
            # e.g. "Quality warning Actors.json:$[1].name: ..."
            parts = msg.split(" ")
            return parts[2] if len(parts) > 2 else "validate"
        if msg.startswith("Saved "):
            return "memory"
        if msg.startswith("Pre-dedup:"):
            return "pipeline"
        if msg.startswith("Translated "):
            return "pipeline"
        if msg.startswith("Retrying") or msg.startswith("retry_after"):
            return "llm"
        if msg.startswith("Error"):
            return "error"
        if msg.startswith("Starting ") or msg.startswith("Finished ") or msg.startswith("Stopped "):
            return "worker"
        return "app"

    _WARN_PREFIXES = ("Quality warning", "Batch failed", "WARN:")

    def _log(self, msg: str, level: str = "INFO") -> None:
        if level == "INFO" and any(msg.startswith(p) for p in self._WARN_PREFIXES):
            level = "WARN"
        log_event(msg, level=level)
        source = self._infer_source(msg)
        self.signals.log_detail.emit(level, source, msg)
        if not any(msg.startswith(p) for p in self._DETAIL_PREFIXES):
            self.signals.log.emit(level, msg)

    def _log_detail(self, msg: str, source: str = "app", level: str = "DEBUG") -> None:
        log_event(msg, level=level)
        self.signals.log_detail.emit(level, source, msg)

    def _on_status(self, text: str) -> None:
        self.status_label.setText(text)

    def _on_progress(self, done: int, total: int) -> None:
        if total <= 0:
            self.progress_bar.setRange(0, 0)
            return
        if self.progress_bar.maximum() != total:
            self.progress_bar.setRange(0, total)
        self.progress_bar.setValue(done)

    def _on_progress_text(self, text: str) -> None:
        self.progress_text_label.setText(text)

    def _on_finished(self) -> None:
        self.current_worker = None
        self._set_running_ui(False)

    def _on_error(self, title: str, body: str) -> None:
        QMessageBox.critical(self, title, body)

    def _on_info(self, title: str, body: str) -> None:
        QMessageBox.information(self, title, body)

    def _on_set_text(self, target: str, value: str) -> None:
        if target == "scan_summary":
            self.scan_summary_label.setText(value)
        elif target == "cheat_status":
            self.cheat_status_label.setText(value)
        elif target == "xunity_status":
            self.xunity_status_label.setText(value)
        elif target == "out_dir":
            self.out_dir_edit.setText(value)
        elif target == "texts_csv":
            self.texts_csv_edit.setText(value)
        elif target == "translations_csv":
            self.translations_csv_edit.setText(value)
        elif target == "game_type":
            self.game_type_combo.setCurrentText(value)

    def _on_set_checked(self, target: str, checked: bool) -> None:
        if target == "restart":
            self.restart_check.setChecked(checked)

    def _set_running_ui(self, running: bool, name: str = "") -> None:
        for btn in self.action_buttons:
            btn.setEnabled(not running)
        # safe_buttons always stay enabled
        self.stop_button.setEnabled(running)
        self.status_label.setText(f"Running: {name}" if running else "Idle")
        self.progress_bar.setVisible(running)
        if running:
            self.translate_progress = {"done": 0, "total": 0, "started": time.monotonic()}
            self.progress_bar.setRange(0, 0)  # indeterminate until we know total
            self.progress_text_label.setText("")
        else:
            self.progress_bar.setRange(0, 1)
            self.progress_bar.setValue(0)
            self.progress_text_label.setText("")

    def _check_stopped(self) -> None:
        if self.stop_requested.is_set():
            raise RuntimeError("Stopped by user")

    def stop_current(self) -> None:
        self.stop_requested.set()
        self._log("Stop requested — finishing current request, then stopping...")
        self.status_label.setText("Stopping...")

    def _run(self, name: str, func: Callable[[], None]) -> None:
        if self.current_worker and self.current_worker.is_alive():
            QMessageBox.warning(self, name, "Another task is already running")
            return
        self.stop_requested.clear()
        self.save_settings()
        self._set_running_ui(True, name)

        def worker() -> None:
            try:
                self._log(f"Starting {name}...")
                func()
                self._log(f"Finished {name}.")
            except RuntimeError as exc:
                if str(exc) == "Stopped by user":
                    self._log(f"Stopped {name}.")
                else:
                    self._log(f"Error in {name}: {exc}")
                    self.signals.error.emit(name, str(exc))
            except Exception as exc:
                self._log(f"Error in {name}: {exc}")
                self.signals.error.emit(name, str(exc))
            finally:
                self.signals.finished.emit()

        self.current_worker = threading.Thread(target=worker, daemon=True)
        self.current_worker.start()

    def open_logs(self) -> None:
        path = logs_dir()
        path.mkdir(parents=True, exist_ok=True)
        open_file_editor(path)

    def _on_api_logging_toggled(self, enabled: bool) -> None:
        set_api_logging(enabled)
        state = "enabled" if enabled else "disabled"
        self._log(f"API call logging {state}")

    def _open_api_log(self) -> None:
        from .app_logging import api_log_file_path
        path = api_log_file_path()
        if path.exists():
            open_file_editor(path)
        else:
            self.signals.info.emit("API Debug Log", f"No API debug log yet for today.\nIt will be created at:\n{path}")

    # ------------------------------------------------------------------
    # Backup management
    # ------------------------------------------------------------------

    def _backup_dirs(self, game_dir: Path) -> list[Path]:
        items: list[Path] = []
        if not game_dir.exists():
            return items
        for child in game_dir.iterdir():
            if child.is_dir() and (child.name.startswith("data_backup_") or child.name.startswith("data_before_restore_")):
                items.append(child)
        items.sort(key=lambda p: p.name)
        return items

    def _selected_backup_dir(self) -> Path:
        items = self.backups_tree.selectedItems()
        if not items:
            raise ValueError("Select a backup first")
        idx = self.backups_tree.indexOfTopLevelItem(items[0])
        if idx < 0 or idx >= len(self.backup_paths):
            raise ValueError("Invalid backup selection")
        return self.backup_paths[idx]

    def _selected_backup_dirs(self) -> list[Path]:
        items = self.backups_tree.selectedItems()
        if not items:
            raise ValueError("Select one or more backups first")
        paths: list[Path] = []
        for it in items:
            idx = self.backups_tree.indexOfTopLevelItem(it)
            if 0 <= idx < len(self.backup_paths):
                paths.append(self.backup_paths[idx])
        return paths

    def _backup_preview(self, paths: list[Path], limit: int = 12) -> str:
        preview = "\n".join(str(p) for p in paths[:limit])
        if len(paths) > limit:
            preview += f"\n...and {len(paths) - limit} more"
        return preview

    def refresh_backups(self) -> None:
        self.backups_tree.clear()
        self.backup_paths = []
        value = self.game_dir_edit.text().strip()
        if not value:
            return
        game_dir = Path(value)
        if not game_dir.exists():
            return
        self.backup_paths = self._backup_dirs(game_dir)
        for path in self.backup_paths:
            kind = "Before restore" if path.name.startswith("data_before_restore_") else "Game backup"
            self.backups_tree.addTopLevelItem(QTreeWidgetItem([kind, str(path)]))

    def create_backup_now(self) -> None:
        try:
            game_dir = self._game_dir_path()
            data_dir = self._game_data_dir(game_dir)
            backup_dir = timestamped_unique_path(game_dir, "data_backup_")
        except Exception as exc:
            QMessageBox.critical(self, "Create Backup", str(exc))
            return
        if QMessageBox.question(self, "Create Backup", f"Create backup now?\n\nFrom: {data_dir}\nTo: {backup_dir}") != QMessageBox.StandardButton.Yes:
            return

        def job() -> None:
            self._check_stopped()
            shutil.copytree(data_dir, backup_dir)
            self._log(f"Backup created -> {backup_dir}")
            self.signals.refresh_backups.emit()
        self._run("create backup", job)

    def open_selected_backup(self) -> None:
        try:
            open_file_editor(self._selected_backup_dir())
        except Exception as exc:
            QMessageBox.critical(self, "Open Selected Backup", str(exc))

    def _delete_backup_paths(self, game_dir: Path, paths: list[Path]) -> None:
        def job() -> None:
            allowed = {p.resolve() for p in self._backup_dirs(game_dir)}
            deleted = 0
            skipped = 0
            for p in paths:
                self._check_stopped()
                if p.resolve() not in allowed or not p.exists():
                    skipped += 1
                    self._log(f"Skipped backup delete -> {p}")
                    continue
                shutil.rmtree(p)
                deleted += 1
                self._log(f"Deleted backup -> {p}")
            self._log(f"Deleted {deleted} backup(s), skipped {skipped}.")
            self.signals.refresh_backups.emit()
        self._run("delete backups", job)

    def delete_selected_backups(self) -> None:
        try:
            game_dir = self._game_dir_path()
            paths = self._selected_backup_dirs()
        except Exception as exc:
            QMessageBox.critical(self, "Delete Backups", str(exc))
            return
        msg = f"Permanently delete {len(paths)} selected backup folder(s)?\n\n{self._backup_preview(paths)}\n\nGame data is not changed. Continue?"
        if QMessageBox.question(self, "Delete Backups", msg) != QMessageBox.StandardButton.Yes:
            return
        self._delete_backup_paths(game_dir, paths)

    def delete_all_backups(self) -> None:
        try:
            game_dir = self._game_dir_path()
            paths = self._backup_dirs(game_dir)
        except Exception as exc:
            QMessageBox.critical(self, "Delete All Backups", str(exc))
            return
        if not paths:
            QMessageBox.information(self, "Delete All Backups", "No backup folders found.")
            return
        msg = f"Permanently delete all {len(paths)} backup folder(s)?\n\n{self._backup_preview(paths)}\n\nGame data is not changed. Continue?"
        if QMessageBox.question(self, "Delete All Backups", msg) != QMessageBox.StandardButton.Yes:
            return
        self._delete_backup_paths(game_dir, paths)

    def restore_backup(self) -> None:
        try:
            game_dir = self._game_dir_path()
            backup_dir = self._selected_backup_dir()
            data_dir = self._game_data_dir(game_dir)
        except Exception as exc:
            QMessageBox.critical(self, "Restore Backup", str(exc))
            return
        if QMessageBox.question(self, "Restore Backup", f"Restore from {backup_dir} to {data_dir}?\nA safety copy will be created first.") != QMessageBox.StandardButton.Yes:
            return

        def job() -> None:
            safety = timestamped_unique_path(game_dir, "data_before_restore_")
            shutil.copytree(data_dir, safety)
            self._log(f"Safety copy -> {safety}")
            for f in backup_dir.rglob("*.json"):
                self._check_stopped()
                dest = data_dir / f.relative_to(backup_dir)
                dest.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(f, dest)
            self._log(f"Restored from {backup_dir}")
            self.signals.refresh_backups.emit()
        self._run("restore backup", job)

    def clear_old_translation(self) -> None:
        targets = [Path(self.texts_csv_edit.text()), Path(self.translations_csv_edit.text()), Path(self.out_dir_edit.text())]
        gd = self.game_dir_edit.text().strip()
        if gd:
            targets.append(Path(gd) / "translator_work" / "translation_memory.csv")
        existing = [p for p in targets if p.exists()]
        if not existing:
            QMessageBox.information(self, "Clear Old Translation", "No old translation files found.")
            return
        preview = "\n".join(str(p) for p in existing[:12])
        if len(existing) > 12:
            preview += f"\n...and {len(existing) - 12} more"
        if QMessageBox.question(self, "Clear Old Translation", f"Delete generated translation files?\n\n{preview}") != QMessageBox.StandardButton.Yes:
            return

        def job() -> None:
            for p in existing:
                self._check_stopped()
                if p.is_dir():
                    shutil.rmtree(p)
                else:
                    p.unlink()
                self._log(f"Deleted -> {p}")
            self.signals.set_checked.emit("restart", True)
        self._run("clear old translation", job)

    def clear_game_memory(self) -> None:
        gd = self.game_dir_edit.text().strip()
        if not gd:
            QMessageBox.warning(self, "Clear Game Memory", "No game folder selected.")
            return
        path = Path(gd) / "translator_work" / "translation_memory.csv"
        if not path.exists():
            QMessageBox.information(self, "Clear Game Memory", f"No game memory file found:\n{path}")
            return
        if QMessageBox.question(self, "Clear Game Memory", f"Delete per-game memory?\n\n{path}\n\nGlobal memory not affected.") != QMessageBox.StandardButton.Yes:
            return
        try:
            path.unlink()
            self._log(f"Deleted game memory -> {path}")
            QMessageBox.information(self, "Clear Game Memory", f"Deleted:\n{path}")
        except Exception as exc:
            QMessageBox.critical(self, "Clear Game Memory", f"Failed: {exc}")

    def clear_global_memory(self) -> None:
        path = global_memory_path()
        if not path.exists():
            QMessageBox.information(self, "Clear Global Memory", f"No global memory file found:\n{path}")
            return
        try:
            with open(path, encoding="utf-8-sig") as f:
                count = sum(1 for _ in csv.reader(f)) - 1
        except Exception:
            count = -1
        cs = f"{count} entries" if count >= 0 else "unknown"
        if QMessageBox.question(self, "Clear Global Memory", f"Delete global memory ({cs})?\n\n{path}\n\nThis affects ALL games.") != QMessageBox.StandardButton.Yes:
            return
        try:
            path.unlink()
            self._log(f"Deleted global memory ({cs}) -> {path}")
            QMessageBox.information(self, "Clear Global Memory", f"Deleted ({cs}):\n{path}")
        except Exception as exc:
            QMessageBox.critical(self, "Clear Global Memory", f"Failed: {exc}")

    # ------------------------------------------------------------------
    # Translation core
    # ------------------------------------------------------------------

    def _extract_entries(self) -> list:
        game_dir = self._game_dir_path()
        gt = self.game_type_combo.currentText()
        if gt == "unity-xunity":
            return extract_xunity(game_dir)
        if normalize_gui_game_type(gt) == "rpg-maker-mz":
            return extract_rpg_maker_mz(game_dir)
        return extract_rpg_maker_mv(game_dir)

    def _dedupe_results(self, results: list[TranslationResult], wanted_ids: set) -> list[TranslationResult]:
        by_id: dict[tuple[str, str], TranslationResult] = {}
        for r in results:
            ident = text_identity(r.file, r.key)
            if ident in wanted_ids:
                by_id[ident] = r
        return list(by_id.values())

    def _translate_entries(self, entries: list[TextEntry], translations_csv: Path) -> list[TranslationResult]:
        gp = self.glossary_path_edit.text().strip()
        options = TranslateOptions(
            target_lang=self.target_lang_edit.text(),
            source_lang=None if self.source_lang_edit.text().lower() == "auto" else self.source_lang_edit.text(),
            provider=self.provider_combo.currentText(),
            model=self.model_edit.text(),
            api_key=self.api_key_edit.text().strip() or None,
            api_base=self.api_base_edit.text().strip() or None,
            batch_size=int(self.batch_size_spin.value()),
            workers=max(1, min(8, int(self.workers_spin.value()))),
            use_memory=self.reuse_memory_check.isChecked(),
            save_memory=self.save_memory_check.isChecked(),
            glossary_path=Path(gp) if gp else None,
            correction_table_path=Path(cp) if (cp := self.correction_table_path_edit.text().strip()) else None,
            restart=self.restart_check.isChecked(),
            on_log=self._log,
            stop_event=self.stop_requested,
        )
        results, report = run_translate(
            entries,
            translations_csv,
            options,
            on_progress=lambda done, total: self.signals.progress.emit(min(done, total), total),
        )
        self._log(
            f"--- Translation report: {report.translated}/{len(entries)} translated, "
            f"{report.fallback} fallback, {report.reused_memory} from memory ---"
        )
        return results

    # ------------------------------------------------------------------
    # Action handlers
    # ------------------------------------------------------------------

    def scan(self) -> None:
        def job() -> None:
            game_dir = self._game_dir_path()
            self.signals.set_text.emit("scan_summary", "Scanning...")
            report = analyze_game(game_dir, self.provider_combo.currentText(), self.target_lang_edit.text())
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

    def auto_translate(self) -> None:
        def job() -> None:
            source = None if self.source_lang_edit.text().lower() == "auto" else self.source_lang_edit.text()
            game_dir = self._game_dir_path()
            self._set_default_work_paths(game_dir, use_signals=True)
            out = auto_translate_game(
                game_dir=game_dir,
                target_lang=self.target_lang_edit.text(),
                source_lang=source,
                provider=self.provider_combo.currentText(),
                model=self.model_edit.text() or self.provider_combo.currentText(),
                api_key=self.api_key_edit.text().strip() or None,
                api_base=self.api_base_edit.text().strip() or None,
                batch_size=int(self.batch_size_spin.value()),
                work_dir=game_dir / "translator_work",
                in_place=False,
                restart=self.restart_check.isChecked(),
                use_memory=self.reuse_memory_check.isChecked(),
                save_memory=self.save_memory_check.isChecked(),
                glossary_path=Path(self.glossary_path_edit.text()) if self.glossary_path_edit.text().strip() else None,
                workers=max(1, min(8, int(self.workers_spin.value()))),
                progress=lambda m: (self._check_stopped(), self._log(m))[1],
            )
            self.signals.set_text.emit("out_dir", str(out))
            self._log(f"Auto translated -> {out}")
        self._run("auto translate export", job)

    def pipeline(self) -> None:
        def job() -> None:
            from .csv_store import save_entries
            entries = self._extract_entries()
            self._check_stopped()
            save_entries(entries, Path(self.texts_csv_edit.text()))
            self._log(f"Extracted {len(entries)} entries")
            results = self._translate_entries(entries, Path(self.translations_csv_edit.text()))
            self._check_stopped()
            out_dir = Path(self.out_dir_edit.text())
            if self.game_type_combo.currentText() == "unity-xunity":
                apply_xunity(results, out_dir)
            else:
                apply_rpg_maker(results, out_dir)
            self._log(f"Exported -> {out_dir}")
        self._run("extract translate export", job)

    def export_translated_data(self) -> None:
        def job() -> None:
            results = load_results(Path(self.translations_csv_edit.text()))
            out_dir = Path(self.out_dir_edit.text())
            if self.game_type_combo.currentText() == "unity-xunity":
                apply_xunity(results, out_dir)
            else:
                apply_rpg_maker(results, out_dir)
            self._log(f"Exported -> {out_dir}")
        self._run("export translated data", job)

    def apply_to_game(self) -> None:
        try:
            game_dir = self._game_dir_path()
            out_dir = Path(self.out_dir_edit.text())
            is_xunity = self.game_type_combo.currentText() == "unity-xunity"
            if is_xunity:
                data_dir = detect_xunity(game_dir)
                if data_dir is None:
                    raise ValueError(f"XUnity Translation folder not found: {game_dir}")
                backup_dir = timestamped_unique_path(game_dir, "translation_backup_")
                file_glob = "*.txt"
                label = "Translation TXT files"
            else:
                data_dir = self._game_data_dir(game_dir)
                backup_dir = timestamped_unique_path(game_dir, "data_backup_")
                file_glob = "*.json"
                label = "RPG Maker JSON files"
        except Exception as exc:
            QMessageBox.critical(self, "Apply to Game", str(exc))
            return
        if QMessageBox.question(self, "Apply to Game", f"This will create a backup, then replace {label}.\n\nFrom: {out_dir}\nTo: {data_dir}\nBackup: {backup_dir}\n\nClose game first. Continue?") != QMessageBox.StandardButton.Yes:
            return

        def job() -> None:
            files = list(out_dir.rglob(file_glob))
            if not files:
                raise ValueError(f"No translated {label} found in: {out_dir}")
            shutil.copytree(data_dir, backup_dir)
            self._log(f"Backup -> {backup_dir}")
            for f in files:
                self._check_stopped()
                dest = data_dir / f.relative_to(out_dir)
                dest.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(f, dest)
            self._log(f"Applied {len(files)} files -> {data_dir}")
            self.signals.refresh_backups.emit()
        self._run("apply to game", job)

    def refresh_cheat_status(self) -> None:
        v = self.game_dir_edit.text().strip()
        if not v:
            self.cheat_status_label.setText("Cheat plugin: no game selected")
            return
        gd = Path(v)
        if not gd.exists():
            self.cheat_status_label.setText("Cheat plugin: game folder not found")
            return
        try:
            status = cheat_status(gd)
        except Exception as exc:
            self.cheat_status_label.setText(f"Cheat status error: {exc}")
            return
        if status.error:
            self.cheat_status_label.setText(f"Cheat plugin: manifest unreadable ({status.error})")
        elif status.installed:
            detail = "OK"
            if status.missing_count or status.modified_count:
                detail = f"{status.missing_count} missing, {status.modified_count} modified"
            self.cheat_status_label.setText(f"Cheat plugin: installed ({status.engine}, {status.release_tag}, {status.file_count} files, {detail})")
        else:
            self.cheat_status_label.setText(f"Cheat plugin: not installed.")

    def apply_cheat_plugin(self) -> None:
        try:
            game_dir = self._game_dir_path()
            engine = detect_cheat_engine(game_dir, self.game_type_combo.currentText())
        except Exception as exc:
            QMessageBox.critical(self, "Apply Cheat", str(exc))
            return
        if QMessageBox.question(self, "Apply Cheat", f"Install cheat plugin to {game_dir}?\nEngine: {engine}") != QMessageBox.StandardButton.Yes:
            return

        def job() -> None:
            manifest = apply_cheat(game_dir, engine, progress=lambda m: (self._check_stopped(), self._log(m))[1])
            self._log(f"Applied cheat {manifest.release_tag} ({len(manifest.files)} files)")
            self.signals.refresh_cheat.emit()
        self._run("apply cheat", job)

    def remove_cheat_plugin(self) -> None:
        try:
            game_dir = self._game_dir_path()
        except Exception as exc:
            QMessageBox.critical(self, "Remove Cheat", str(exc))
            return
        if QMessageBox.question(self, "Remove Cheat", f"Remove cheat plugin from {game_dir}?") != QMessageBox.StandardButton.Yes:
            return

        def job() -> None:
            manifest = remove_cheat(game_dir, progress=lambda m: (self._check_stopped(), self._log(m))[1])
            self._log(f"Removed cheat ({len(manifest.files)} tracked files)")
            self.signals.refresh_cheat.emit()
        self._run("remove cheat", job)

    def refresh_xunity_status(self) -> None:
        v = self.game_dir_edit.text().strip()
        if not v:
            self.xunity_status_label.setText("XUnity: no game selected")
            return
        gd = Path(v)
        if not gd.exists():
            self.xunity_status_label.setText("XUnity: game folder not found")
            return
        try:
            s = xunity_install_status(gd)
        except Exception as exc:
            self.xunity_status_label.setText(f"XUnity status error: {exc}")
            return
        status = s.get("status", "")
        if status == "installed":
            has_txt = s.get("has_translation_dir", False)
            txt_note = " | Translation folder present — ready to scan!" if has_txt else " | Run game once to collect text"
            self.xunity_status_label.setText(
                f"XUnity installed: BepInEx {s['bepinex_tag']}, XUnity {s['xunity_tag']}{txt_note}"
            )
            self.xunity_status_label.setStyleSheet("color: #1a7a1a;")
        elif status == "manual":
            has_txt = s.get("has_translation_dir", False)
            txt_note = " | Translation folder present" if has_txt else " | Run game once to collect text"
            self.xunity_status_label.setText(f"BepInEx detected (manual install){txt_note}")
            self.xunity_status_label.setStyleSheet("color: #7a6000;")
        elif status == "not_installed":
            self.xunity_status_label.setText("XUnity: not installed — click 'Install BepInEx + XUnity...' to set up")
            self.xunity_status_label.setStyleSheet("color: #555;")
        else:
            self.xunity_status_label.setText("XUnity: not a Unity game (RPG Maker or unknown engine)")
            self.xunity_status_label.setStyleSheet("color: #555;")

    def install_xunity_plugin(self) -> None:
        try:
            game_dir = self._game_dir_path()
        except Exception as exc:
            QMessageBox.critical(self, "Install XUnity", str(exc))
            return
        msg = (
            f"Install BepInEx + XUnity.AutoTranslator into:\n{game_dir}\n\n"
            "This will download ~30MB from GitHub and extract files into the game folder.\n"
            "After installing, run the game ONCE so XUnity collects text, then scan again."
        )
        if QMessageBox.question(self, "Install BepInEx + XUnity", msg) != QMessageBox.StandardButton.Yes:
            return
        target_lang = self.target_lang_edit.text().strip() or "vi"
        src = self.source_lang_edit.text().strip()
        from_lang = "auto" if not src or src.lower() == "auto" else src
        from_lang = from_lang if from_lang != "auto" else "ja"

        def job() -> None:
            manifest = install_xunity(
                game_dir,
                progress=lambda m: (self._check_stopped(), self._log(m))[1],
                target_lang=target_lang,
                from_lang=from_lang,
            )
            self._log(f"BepInEx {manifest.bepinex_tag} + XUnity {manifest.xunity_tag} installed ({len(manifest.files)} files)")
            self.signals.refresh_xunity.emit()
        self._run("install xunity", job)

    def fix_xunity_config(self) -> None:
        try:
            game_dir = self._game_dir_path()
        except Exception as exc:
            QMessageBox.critical(self, "Fix XUnity Config", str(exc))
            return
        target_lang = self.target_lang_edit.text().strip() or "vi"
        src = self.source_lang_edit.text().strip()
        from_lang = src if src and src.lower() != "auto" else "ja"
        lang_code = resolve_xunity_lang(target_lang)
        from_code = resolve_xunity_lang(from_lang)
        msg = (
            f"Rewrite XUnity AutoTranslatorConfig.ini for:\n{game_dir}\n\n"
            f"From: {from_lang} → {from_code}\n"
            f"To:   {target_lang} → {lang_code}\n\n"
            "This will overwrite the existing config. Run the game again after fixing."
        )
        if QMessageBox.question(self, "Fix XUnity Config", msg) != QMessageBox.StandardButton.Yes:
            return

        def job() -> None:
            _write_xunity_config(game_dir, target_lang, from_lang, progress=lambda m: self._log(m))
            self._log(f"XUnity config fixed: {from_code} -> {lang_code}")
            self.signals.refresh_xunity.emit()
        self._run("fix xunity config", job)

    def uninstall_xunity_plugin(self) -> None:
        try:
            game_dir = self._game_dir_path()
        except Exception as exc:
            QMessageBox.critical(self, "Uninstall XUnity", str(exc))
            return
        if QMessageBox.question(self, "Uninstall XUnity", f"Remove BepInEx + XUnity from {game_dir}?") != QMessageBox.StandardButton.Yes:
            return

        def job() -> None:
            manifest = uninstall_xunity(game_dir, progress=lambda m: (self._check_stopped(), self._log(m))[1])
            self._log(f"XUnity uninstalled ({len(manifest.files)} tracked files removed)")
            self.signals.refresh_xunity.emit()
        self._run("uninstall xunity", job)

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


# ----------------------------------------------------------------------
# TranslationEditor dialog
# ----------------------------------------------------------------------


class TranslationEditor(QDialog):
    def __init__(self, parent: TranslatorGUI, path: Path) -> None:
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


def main() -> None:
    app = QApplication(sys.argv)
    app.setStyle("Fusion")
    win = TranslatorGUI()
    win.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
