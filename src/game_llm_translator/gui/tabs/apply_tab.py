from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from PySide6.QtWidgets import (
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QVBoxLayout,
    QWidget,
)

from ...csv_store import load_results
from ...font_replacement import ensure_game_fonts_support
from ...path_utils import timestamped_unique_path
from ...rpg_maker import apply_rpg_maker
from ...rpg_maker_cheat import (
    apply_cheat,
    cheat_status,
    detect_cheat_engine,
    remove_cheat,
)
from ...unity_setup import (
    _write_xunity_config,
    install_xunity,
    resolve_xunity_lang,
    uninstall_xunity,
    xunity_install_status,
)
from ...xunity import apply_xunity, detect_xunity

if TYPE_CHECKING:
    from ..signals import WorkerSignals


class ApplyTabMixin:
    translations_csv_edit: QLineEdit
    out_dir_edit: QLineEdit
    game_dir_edit: QLineEdit
    game_type_combo: ...
    cheat_status_label: QLabel
    xunity_status_label: QLabel
    target_lang_edit: QLineEdit
    source_lang_edit: QLineEdit
    signals: WorkerSignals

    def _build_apply_tab(self) -> QWidget:
        tab = QWidget()
        outer = QVBoxLayout(tab)

        export = QGroupBox("Export translated data (safe — writes to output folder)")
        e = QHBoxLayout(export)
        e.addWidget(self._primary_button("Export", self.export_translated_data))
        e.addStretch()
        outer.addWidget(export)

        risky = QGroupBox("⚠ Apply translated data to game (modifies game files)")
        risky.setStyleSheet(
            "QGroupBox { border: 1px solid #c44; border-radius: 4px; margin-top: 8px; padding-top: 8px; }"
            "QGroupBox::title { subcontrol-origin: margin; subcontrol-position: top left; color: #c44; left: 8px; padding: 0 4px; }"
        )
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

        xunity = QGroupBox("⚠ BepInEx + XUnity.AutoTranslator (modifies Unity game files)")
        xunity.setStyleSheet(
            "QGroupBox { border: 1px solid #c44; border-radius: 4px; margin-top: 8px; padding-top: 8px; }"
            "QGroupBox::title { subcontrol-origin: margin; subcontrol-position: top left; color: #c44; left: 8px; padding: 0 4px; }"
        )
        xu = QVBoxLayout(xunity)
        xu_label = QLabel(
            "Automatically installs BepInEx + XUnity.AutoTranslator into a Unity game.\n"
            "After installing: run the game once so XUnity collects text, then scan again."
        )
        xu_label.setWordWrap(True)
        xu.addWidget(xu_label)
        self.xunity_status_label = QLabel("XUnity: no game selected")
        self.xunity_status_label.setWordWrap(True)
        self.xunity_status_label.setStyleSheet("color: palette(placeholder-text);")
        xu.addWidget(self.xunity_status_label)
        xu_row = QHBoxLayout()
        xu_row.addWidget(self._action_button("Install BepInEx + XUnity...", self.install_xunity_plugin))
        xu_row.addWidget(self._action_button("Fix Config / Language", self.fix_xunity_config))
        xu_row.addWidget(self._action_button("Uninstall XUnity", self.uninstall_xunity_plugin))
        xu_row.addWidget(self._action_button("Refresh", self.refresh_xunity_status))
        xu_row.addStretch()
        xu.addLayout(xu_row)
        outer.addWidget(xunity)

        cheat = QGroupBox("⚠ Cheat plugin (modifies RPG Maker MV/MZ game files)")
        cheat.setStyleSheet(
            "QGroupBox { border: 1px solid #c44; border-radius: 4px; margin-top: 8px; padding-top: 8px; }"
            "QGroupBox::title { subcontrol-origin: margin; subcontrol-position: top left; color: #c44; left: 8px; padding: 0 4px; }"
        )
        c = QVBoxLayout(cheat)
        c_label = QLabel("Installs RPG Maker MV/MZ Cheat UI Plugin. Toggle in game: Ctrl+C. Remove uses this app's manifest only.")
        c_label.setWordWrap(True)
        c.addWidget(c_label)
        self.cheat_status_label = QLabel("Cheat plugin: no game selected")
        self.cheat_status_label.setWordWrap(True)
        self.cheat_status_label.setStyleSheet("color: palette(placeholder-text);")
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
            import shutil
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
            # Auto font replacement for Vietnamese
            try:
                font_result = ensure_game_fonts_support(game_dir, self.target_lang_edit.text())
                if font_result["replaced"]:
                    self._log(f"Font replaced: {font_result.get('details', '')}")
                else:
                    self._log(f"Font check: {font_result.get('details', '')}")
            except Exception as exc:
                self._log(f"Font replacement skipped: {exc}")
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
            self.xunity_status_label.setStyleSheet("color: palette(placeholder-text);")
        else:
            self.xunity_status_label.setText("XUnity: not a Unity game (RPG Maker or unknown engine)")
            self.xunity_status_label.setStyleSheet("color: palette(placeholder-text);")

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