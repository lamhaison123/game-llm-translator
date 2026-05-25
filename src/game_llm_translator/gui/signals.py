from __future__ import annotations

from PySide6.QtCore import QObject, Signal

from ..models import TranslationResult


class WorkerSignals(QObject):
    log = Signal(str, str)              # level, message
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
    result_batch = Signal(list)  # list[TranslationResult] — live batch results for preview