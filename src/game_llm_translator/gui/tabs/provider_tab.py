from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ..signals import WorkerSignals

from typing import Callable

from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QSpinBox,
    QWidget,
)

from ...app_config import save_app_config
from ...app_logging import set_api_logging


class ProviderTabMixin:
    signals: WorkerSignals
    config: dict
    provider_combo: QComboBox
    model_edit: QLineEdit
    api_key_edit: QLineEdit
    api_base_edit: QLineEdit
    source_lang_edit: QLineEdit
    target_lang_edit: QLineEdit
    game_type_combo: QComboBox
    game_dir_edit: QLineEdit
    texts_csv_edit: QLineEdit
    translations_csv_edit: QLineEdit
    out_dir_edit: QLineEdit
    remember_api_check: QCheckBox
    log_api_check: QCheckBox
    batch_size_spin: QSpinBox
    workers_spin: QSpinBox
    glossary_path_edit: QLineEdit
    correction_table_path_edit: QLineEdit
    reuse_memory_check: QCheckBox
    save_memory_check: QCheckBox
    restart_check: QCheckBox
    _log: Callable
    _safe_button: Callable

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
        if hasattr(self, "preview_source_edit"):
            data.update({
                "preview_source": self.preview_source_edit.text(),
                "preview_output": self.preview_output_edit.text(),
                "preview_target_lang": self.preview_target_edit.text(),
                "preview_provider": self.preview_provider_combo.currentText(),
                "preview_model": self.preview_model_edit.text(),
                "preview_workers": int(self.preview_workers_spin.value()),
            })
        if self.remember_api_check.isChecked():
            data["api_key"] = self.api_key_edit.text()
            data["api_base"] = self.api_base_edit.text()
        save_app_config(data)
        self._log("Settings saved. API key/base are stored as plaintext on this machine.")
        return data

    def _on_api_logging_toggled(self, enabled: bool) -> None:
        set_api_logging(enabled)
        state = "enabled" if enabled else "disabled"
        self._log(f"API call logging {state}")

    def _open_api_log(self) -> None:
        from ...app_logging import api_log_file_path
        from ...editor import open_file_editor
        path = api_log_file_path()
        if path.exists():
            open_file_editor(path)
        else:
            self.signals.info.emit("API Debug Log", f"No API debug log yet for today.\nIt will be created at:\n{path}")