from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ..signals import WorkerSignals

import threading
from pathlib import Path
from typing import Callable

from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QFileDialog,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from ...auto import auto_translate_game
from ...csv_store import save_entries
from ...models import TextEntry, TranslationResult, text_identity
from ...rpg_maker import apply_rpg_maker, normalize_gui_game_type, extract_rpg_maker_mv, extract_rpg_maker_mz
from ...translate_pipeline import TranslateOptions, run_translate
from ...xunity import apply_xunity, extract_xunity


class TranslateTabMixin:
    signals: WorkerSignals
    config: dict
    game_type_combo: QComboBox
    game_dir_edit: QLineEdit
    texts_csv_edit: QLineEdit
    translations_csv_edit: QLineEdit
    out_dir_edit: QLineEdit
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
    restart_check: QCheckBox
    reuse_memory_check: QCheckBox
    save_memory_check: QCheckBox
    stop_requested: threading.Event
    _log: Callable
    _check_stopped: Callable
    _run: Callable
    _game_dir_path: Callable
    _path_picker: Callable
    _action_button: Callable
    _set_default_work_paths: Callable

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

    def _choose_glossary(self) -> None:
        value, _ = QFileDialog.getOpenFileName(self, "Select glossary CSV", self.glossary_path_edit.text(), "CSV files (*.csv);;All files (*)")
        if value:
            self.glossary_path_edit.setText(value)

    def _choose_correction_table(self) -> None:
        value, _ = QFileDialog.getOpenFileName(self, "Select correction table CSV", self.correction_table_path_edit.text(), "CSV files (*.csv);;All files (*)")
        if value:
            self.correction_table_path_edit.setText(value)

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