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
    QMessageBox,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from ...auto import auto_translate_game
from ...csv_store import load_results, save_entries, save_results
from ...models import TextEntry, TranslationResult, text_identity
from ...rpg_maker import apply_rpg_maker, normalize_gui_game_type, extract_rpg_maker_mv, extract_rpg_maker_mz
from ...translate_pipeline import TranslateOptions, run_translate
from ...validate import needs_retry
from ...xunity import apply_xunity, extract_xunity
from ..paths import normalize_path_text


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
    _primary_button: Callable
    _set_default_work_paths: Callable

    # ----- Translate tab -----

    def _build_translate_tab(self) -> QWidget:
        tab = QWidget()
        outer = QVBoxLayout(tab)

        actions = QHBoxLayout()
        auto_btn = self._primary_button("Auto-translate", self.auto_translate)
        auto_btn.setToolTip("Extract + translate + export in one step (recommended).")
        actions.addWidget(auto_btn)
        pipeline_btn = self._action_button("Translate (full pipeline)", self.pipeline)
        pipeline_btn.setToolTip("Extract → translate → export. Same as Auto-translate but uses current Setup paths explicitly.")
        actions.addWidget(pipeline_btn)
        retry_btn = self._action_button("Retry flagged", self.retry_flagged_rows)
        retry_btn.setToolTip("Re-translate rows where target==source or target still contains CJK characters.")
        actions.addWidget(retry_btn)
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

        self.glossary_path_edit = QLineEdit(normalize_path_text(str(self.config.get("glossary_path", ""))))
        adv.addRow("Glossary CSV (optional)", self._path_picker(self.glossary_path_edit, self._choose_glossary))

        self.correction_table_path_edit = QLineEdit(normalize_path_text(str(self.config.get("correction_table_path", ""))))
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
        hint.setStyleSheet("color: palette(placeholder-text);")
        adv.addRow("", hint)

        outer.addWidget(advanced)
        outer.addStretch()
        return tab

    def _choose_glossary(self) -> None:
        value, _ = QFileDialog.getOpenFileName(self, "Select glossary CSV", self.glossary_path_edit.text(), "CSV files (*.csv);;All files (*)")
        if value:
            self.glossary_path_edit.setText(normalize_path_text(value))

    def _choose_correction_table(self) -> None:
        value, _ = QFileDialog.getOpenFileName(self, "Select correction table CSV", self.correction_table_path_edit.text(), "CSV files (*.csv);;All files (*)")
        if value:
            self.correction_table_path_edit.setText(normalize_path_text(value))

    def _extract_entries(self, game_dir: Path, game_type: str) -> list:
        if game_type == "unity-xunity":
            return extract_xunity(game_dir)
        normalized = normalize_gui_game_type(game_type)
        if normalized == "rpg-maker-mz":
            return extract_rpg_maker_mz(game_dir)
        return extract_rpg_maker_mv(game_dir)

    def _build_translate_options(self) -> TranslateOptions:
        """Snapshot widget values on the main thread into a TranslateOptions.

        Must be called before spawning a worker — never from a worker thread.
        """
        gp = self.glossary_path_edit.text().strip()
        cp = self.correction_table_path_edit.text().strip()
        return TranslateOptions(
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
            correction_table_path=Path(cp) if cp else None,
            restart=self.restart_check.isChecked(),
            on_log=self._log,
            stop_event=self.stop_requested,
        )

    def _dedupe_results(self, results: list[TranslationResult], wanted_ids: set) -> list[TranslationResult]:
        by_id: dict[tuple[str, str], TranslationResult] = {}
        for r in results:
            ident = text_identity(r.file, r.key)
            if ident in wanted_ids:
                by_id[ident] = r
        return list(by_id.values())

    def _translate_entries(self, entries: list[TextEntry], translations_csv: Path, options: TranslateOptions) -> list[TranslationResult]:
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
        # Read widget values on the main thread before starting the worker
        source_lang_raw = self.source_lang_edit.text()
        source = None if source_lang_raw.lower() == "auto" else source_lang_raw
        target_lang = self.target_lang_edit.text()
        provider = self.provider_combo.currentText()
        model = self.model_edit.text() or self.provider_combo.currentText()
        api_key = self.api_key_edit.text().strip() or None
        api_base = self.api_base_edit.text().strip() or None
        batch_size = int(self.batch_size_spin.value())
        workers = max(1, min(8, int(self.workers_spin.value())))
        restart = self.restart_check.isChecked()
        use_memory = self.reuse_memory_check.isChecked()
        save_memory = self.save_memory_check.isChecked()
        glossary_path = Path(self.glossary_path_edit.text()) if self.glossary_path_edit.text().strip() else None

        def job() -> None:
            game_dir = self._game_dir_path()
            self._set_default_work_paths(game_dir, use_signals=True)
            out = auto_translate_game(
                game_dir=game_dir,
                target_lang=target_lang,
                source_lang=source,
                provider=provider,
                model=model,
                api_key=api_key,
                api_base=api_base,
                batch_size=batch_size,
                work_dir=game_dir / "translator_work",
                in_place=False,
                restart=restart,
                use_memory=use_memory,
                save_memory=save_memory,
                glossary_path=glossary_path,
                workers=workers,
                progress=lambda m: (self._check_stopped(), self._log(m))[1],
            )
            self.signals.set_text.emit("out_dir", str(out))
            self._log(f"Auto translated -> {out}")
        self._run("auto translate export", job)

    def pipeline(self) -> None:
        # Read widget values on the main thread before starting the worker
        game_type = self.game_type_combo.currentText()
        game_dir = self._game_dir_path()
        texts_csv = self.texts_csv_edit.text()
        translations_csv = self.translations_csv_edit.text()
        out_dir_value = self.out_dir_edit.text()
        options = self._build_translate_options()

        def job() -> None:
            entries = self._extract_entries(game_dir, game_type)
            self._check_stopped()
            save_entries(entries, Path(texts_csv))
            self._log(f"Extracted {len(entries)} entries")
            results = self._translate_entries(entries, Path(translations_csv), options)
            self._check_stopped()
            out_dir = Path(out_dir_value)
            if game_type == "unity-xunity":
                apply_xunity(results, out_dir)
            else:
                apply_rpg_maker(results, out_dir)
            self._log(f"Exported -> {out_dir}")
        self._run("extract translate export", job)

    def retry_flagged_rows(self) -> None:
        """Clear targets of fallback + CJK-leak rows in translations CSV, then re-run translate."""
        translations_csv = Path(self.translations_csv_edit.text())
        if not translations_csv.exists():
            QMessageBox.warning(self, "Retry flagged", f"File not found: {translations_csv}")
            return
        existing = load_results(translations_csv)
        if not existing:
            QMessageBox.warning(self, "Retry flagged", f"{translations_csv} is empty.")
            return
        flagged_ids = {
            text_identity(r.file, r.key)
            for r in existing
            if needs_retry(r.source, r.target)
        }
        if not flagged_ids:
            QMessageBox.information(self, "Retry flagged", "No fallback or CJK-leak rows found.")
            return
        reply = QMessageBox.question(
            self,
            "Retry flagged rows",
            f"Re-translate {len(flagged_ids)} flagged row(s) in {translations_csv.name}?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return

        # Snapshot widget values on the main thread before spawning the worker
        options = self._build_translate_options()

        def job() -> None:
            rows = load_results(translations_csv)
            rewritten: list[TranslationResult] = []
            cleared = 0
            for r in rows:
                if text_identity(r.file, r.key) in flagged_ids:
                    rewritten.append(TranslationResult(r.file, r.key, r.source, "", r.context, sub_keys=r.sub_keys))
                    cleared += 1
                else:
                    rewritten.append(r)
            save_results(rewritten, translations_csv)
            self._log(f"Cleared {cleared} flagged row(s) — re-running translate pipeline.")
            entries = [TextEntry(r.file, r.key, r.source, r.context, "") for r in rewritten]
            self._translate_entries(entries, translations_csv, options)
            self._log(f"Retry done -> {translations_csv}")
        self._run("retry flagged rows", job)