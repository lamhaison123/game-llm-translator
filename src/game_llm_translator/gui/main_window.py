from __future__ import annotations

import shutil
import sys
import threading
import time
from pathlib import Path
from typing import Any, Callable, cast

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QColor, QFont, QIcon, QPalette
from PySide6.QtWidgets import (
    QApplication,
    QFileDialog,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QProgressBar,
    QTabWidget,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from .signals import WorkerSignals
from ..app_config import load_app_config, save_app_config
from ..app_logging import log_event
from ..editor import open_file_editor
from ..path_utils import timestamped_unique_path

from .tabs.game_tab import GameTabMixin
from .tabs.provider_tab import ProviderTabMixin
from .tabs.preview_tab import PreviewTabMixin
from .tabs.translate_tab import TranslateTabMixin
from .tabs.review_tab import ReviewTabMixin
from .tabs.apply_tab import ApplyTabMixin
from .tabs.backups_tab import BackupsTabMixin
from .tabs.log_tabs import LogTabsMixin


def _resource_dir(name: str) -> Path:
    bundle_dir = getattr(sys, "_MEIPASS", None)
    if bundle_dir:
        return Path(bundle_dir) / name
    return Path(__file__).resolve().parent.parent.parent / name


class TranslatorGUI(
    QMainWindow,
    GameTabMixin,
    ProviderTabMixin,
    PreviewTabMixin,
    TranslateTabMixin,
    ReviewTabMixin,
    ApplyTabMixin,
    BackupsTabMixin,
    LogTabsMixin,
):
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

    # ------------------------------------------------------------------
    # Window setup / theme
    # ------------------------------------------------------------------

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

        # Header — app title + game name + step indicator + theme toggle
        header = QHBoxLayout()
        title_block = QVBoxLayout()
        title_block.setSpacing(0)
        title = QLabel("Game LLM Translator")
        font = title.font()
        font.setPointSize(10)
        title.setFont(font)
        title.setStyleSheet("color: palette(placeholder-text);")
        title_block.addWidget(title)
        self.game_name_label = QLabel("(no game selected)")
        gfont = self.game_name_label.font()
        gfont.setPointSize(14)
        gfont.setBold(True)
        self.game_name_label.setFont(gfont)
        title_block.addWidget(self.game_name_label)
        header.addLayout(title_block)
        header.addStretch()
        self.step_label = QLabel("")
        sfont = self.step_label.font()
        sfont.setPointSize(11)
        self.step_label.setFont(sfont)
        self.step_label.setStyleSheet("color: palette(highlight);")
        header.addWidget(self.step_label)
        header.addSpacing(12)
        self.theme_button = QPushButton("Toggle theme")
        self.theme_button.clicked.connect(self._toggle_theme)
        header.addWidget(self.theme_button)
        root.addLayout(header)

        # Tabs
        self.tabs = QTabWidget()
        self.tabs.addTab(self._build_setup_tab(), "Setup")
        self.tabs.addTab(self._build_translate_tab(), "Translate")
        self.tabs.addTab(self._build_preview_tab(), "Review")
        self.tabs.addTab(self._build_apply_tab(), "Apply")
        self.tabs.addTab(self._build_tools_tab(), "Tools")
        self.tabs.addTab(self._build_logs_tab(), "Logs")
        self.tabs.currentChanged.connect(self._refresh_header)
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
        self.stop_button = QPushButton("■ Stop")
        self.stop_button.setEnabled(False)
        self.stop_button.setMinimumWidth(80)
        self.stop_button.setStyleSheet(
            "QPushButton:enabled { background-color: #c44; color: white; font-weight: bold; }"
        )
        self.stop_button.clicked.connect(self.stop_current)
        bar.addPermanentWidget(self.stop_button)

        # When game_dir changes, refresh header.
        self.game_dir_edit.textChanged.connect(self._refresh_header)
        self._refresh_header()

    def _refresh_header(self, *_args) -> None:
        """Update game name + step indicator in the header."""
        if not hasattr(self, "game_name_label"):
            return
        game_path = self.game_dir_edit.text().strip() if hasattr(self, "game_dir_edit") else ""
        if game_path:
            name = Path(game_path).name or game_path
            self.game_name_label.setText(name)
            self.game_name_label.setToolTip(game_path)
        else:
            self.game_name_label.setText("(no game selected)")
            self.game_name_label.setToolTip("")
        if hasattr(self, "tabs"):
            steps = ["1. Setup", "2. Translate", "3. Review", "4. Apply", "Tools", "Logs"]
            idx = self.tabs.currentIndex()
            if 0 <= idx < len(steps):
                self.step_label.setText(steps[idx])
            else:
                self.step_label.setText("")

    # ------------------------------------------------------------------
    # Composite tabs (merge multiple mixin builders into one tab page)
    # ------------------------------------------------------------------

    def _build_setup_tab(self) -> QWidget:
        """Setup = Game group + Provider group, side-by-side stacked."""
        tab = QWidget()
        outer = QVBoxLayout(tab)
        outer.setContentsMargins(8, 8, 8, 8)

        game_group = QGroupBox("Game")
        gl = QVBoxLayout(game_group)
        gl.setContentsMargins(8, 12, 8, 8)
        gl.addWidget(self._build_game_tab())
        outer.addWidget(game_group)

        provider_group = QGroupBox("Provider && Languages")
        pl = QVBoxLayout(provider_group)
        pl.setContentsMargins(8, 12, 8, 8)
        pl.addWidget(self._build_provider_tab())
        outer.addWidget(provider_group)

        outer.addStretch()
        return tab

    def _build_tools_tab(self) -> QWidget:
        """Tools = Backups + Memory cleanup (merged from old Backups tab)."""
        # The existing _build_recovery_tab already contains both groups.
        return self._build_recovery_tab()

    # ------------------------------------------------------------------
    # Shared widget helpers
    # ------------------------------------------------------------------

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

    _PRIMARY_BUTTON_QSS = (
        "QPushButton { background-color: #5b6cff; color: white; font-weight: bold;"
        " border: none; padding: 6px 14px; border-radius: 4px; }"
        "QPushButton:hover:enabled { background-color: #4858e0; }"
        "QPushButton:pressed:enabled { background-color: #3a48b8; }"
        "QPushButton:disabled { background-color: #6f7693; color: #d8dcec; }"
    )

    def _primary_button(self, text: str, slot: Callable[[], None]) -> QPushButton:
        """An action button styled as the tab's primary CTA."""
        btn = self._action_button(text, slot)
        btn.setStyleSheet(self._PRIMARY_BUTTON_QSS)
        return btn

    def _safe_button(self, text: str, slot: Callable[[], None]) -> QPushButton:
        btn = QPushButton(text)
        btn.clicked.connect(slot)
        self.safe_buttons.append(btn)
        return btn

    # ------------------------------------------------------------------
    # Shared state helpers
    # ------------------------------------------------------------------

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
        if msg.startswith("Batch failed"):
            return "pipeline"
        if msg.startswith("Quality warning"):
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
        if hasattr(self, "preview_stop_btn"):
            self.preview_stop_btn.setEnabled(False)

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

    # ------------------------------------------------------------------
    # Worker management
    # ------------------------------------------------------------------

    def _set_running_ui(self, running: bool, name: str = "") -> None:
        for btn in self.action_buttons:
            btn.setEnabled(not running)
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
        from ..app_logging import logs_dir
        path = logs_dir()
        path.mkdir(parents=True, exist_ok=True)
        open_file_editor(path)