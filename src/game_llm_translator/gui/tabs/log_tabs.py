from __future__ import annotations

from typing import TYPE_CHECKING

from PySide6.QtCore import QTimer
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QHBoxLayout,
    QLabel,
    QPlainTextEdit,
    QPushButton,
    QTabWidget,
    QTreeWidget,
    QVBoxLayout,
    QWidget,
)

from ...app_logging import api_log_file_path

if TYPE_CHECKING:
    from ..signals import WorkerSignals


class LogTabsMixin:
    tabs: QTabWidget
    log_view: QTreeWidget
    detail_log_view: QTreeWidget
    api_log_view: QPlainTextEdit
    _api_log_follow: QCheckBox
    _api_log_file_size: int
    _api_log_timer: QTimer
    _log_row_limit: int
    _detail_row_limit: int
    _logs_sub_tabs: QTabWidget
    signals: WorkerSignals

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

    def _build_logs_tab(self) -> QWidget:
        """Single Logs tab containing 3 sub-views: Activity / Detail / API Log."""
        tab = QWidget()
        outer = QVBoxLayout(tab)
        outer.setContentsMargins(0, 0, 0, 0)
        self._logs_sub_tabs = QTabWidget()
        self._logs_sub_tabs.addTab(self._build_activity_view(), "Activity")
        self._logs_sub_tabs.addTab(self._build_detail_view(), "Detail")
        self._logs_sub_tabs.addTab(self._build_api_log_view(), "API Log")
        self._logs_sub_tabs.currentChanged.connect(self._on_logs_sub_tab_changed)
        outer.addWidget(self._logs_sub_tabs, 1)

        self._api_log_file_size = 0
        self._api_log_timer = QTimer(self)
        self._api_log_timer.setInterval(2000)
        self._api_log_timer.timeout.connect(self._api_log_poll)

        self.tabs.currentChanged.connect(self._on_tab_changed)
        return tab

    def _build_activity_view(self) -> QWidget:
        view = QWidget()
        outer = QVBoxLayout(view)
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
        return view

    def _build_detail_view(self) -> QWidget:
        view = QWidget()
        outer = QVBoxLayout(view)
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
        return view

    def _build_api_log_view(self) -> QWidget:
        view = QWidget()
        outer = QVBoxLayout(view)

        toolbar = QHBoxLayout()
        toolbar.addWidget(self._safe_button("Refresh", self._api_log_refresh))
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
        hint.setStyleSheet("color: palette(placeholder-text); font-size: 11px;")
        toolbar.addWidget(hint)

        outer.addLayout(toolbar)

        self.api_log_view = QPlainTextEdit()
        self.api_log_view.setReadOnly(True)
        self.api_log_view.setFont(QFont("Consolas", 8))
        outer.addWidget(self.api_log_view, 1)
        return view

    def _is_api_log_visible(self) -> bool:
        """True when the API Log sub-tab inside the Logs tab is the visible view."""
        if not hasattr(self, "_logs_sub_tabs"):
            return False
        logs_tab_index = self._find_logs_tab_index()
        if logs_tab_index < 0 or self.tabs.currentIndex() != logs_tab_index:
            return False
        return self._logs_sub_tabs.tabText(self._logs_sub_tabs.currentIndex()) == "API Log"

    def _find_logs_tab_index(self) -> int:
        for i in range(self.tabs.count()):
            if self.tabs.tabText(i) == "Logs":
                return i
        return -1

    def _on_tab_changed(self, _index: int) -> None:
        if self._is_api_log_visible():
            self._api_log_refresh()
            self._api_log_timer.start()
        else:
            self._api_log_timer.stop()

    def _on_logs_sub_tab_changed(self, _index: int) -> None:
        self._on_tab_changed(self.tabs.currentIndex())

    def _api_log_poll(self) -> None:
        path = api_log_file_path()
        try:
            size = path.stat().st_size
        except (FileNotFoundError, OSError):
            return
        if size != self._api_log_file_size:
            self._api_log_refresh()

    def _api_log_refresh(self) -> None:
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
