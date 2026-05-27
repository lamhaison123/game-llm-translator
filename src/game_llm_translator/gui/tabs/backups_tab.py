from __future__ import annotations

import csv
import shutil
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ..signals import WorkerSignals

from typing import Callable

from PySide6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QFileDialog,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from ...editor import open_file_editor
from ...path_utils import timestamped_unique_path
from ...translation_memory import global_memory_path


class BackupsTabMixin:
    signals: WorkerSignals
    config: dict
    game_dir_edit: QLineEdit
    texts_csv_edit: QLineEdit
    translations_csv_edit: QLineEdit
    out_dir_edit: QLineEdit
    backup_paths: list[Path]
    backups_tree: QTreeWidget
    restart_check: QCheckBox
    _log: Callable
    _check_stopped: Callable
    _run: Callable
    _game_dir_path: Callable
    _game_data_dir: Callable
    _action_button: Callable
    _safe_button: Callable

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

        memory_group = QGroupBox("⚠ Memory && translation cleanup (destructive — deletes generated data)")
        memory_group.setStyleSheet(
            "QGroupBox { border: 1px solid #c44; border-radius: 4px; margin-top: 8px; padding-top: 8px; }"
            "QGroupBox::title { subcontrol-origin: margin; subcontrol-position: top left; color: #c44; left: 8px; padding: 0 4px; }"
        )
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