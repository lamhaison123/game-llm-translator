from __future__ import annotations

from types import SimpleNamespace

from PySide6.QtWidgets import QApplication

from game_llm_translator.gui.main_window import TranslatorGUI
from game_llm_translator.gui.tabs.provider_tab import ProviderTabMixin


def _app():
    app = QApplication.instance() or QApplication([])
    return app


def test_open_logs_imports_app_logging(monkeypatch, tmp_path):
    _app()

    opened = []
    monkeypatch.setattr("game_llm_translator.gui.main_window.open_file_editor", opened.append)
    monkeypatch.setattr("game_llm_translator.app_logging.logs_dir", lambda: tmp_path / "logs")

    widget = SimpleNamespace()
    TranslatorGUI.open_logs(widget)

    assert opened == [tmp_path / "logs"]


def test_save_settings_persists_preview_fields(monkeypatch):
    saved = []
    monkeypatch.setattr("game_llm_translator.gui.tabs.provider_tab.save_app_config", saved.append)

    widget = SimpleNamespace(
        game_type_combo=SimpleNamespace(currentText=lambda: "rpg-maker-mv"),
        game_dir_edit=SimpleNamespace(text=lambda: "game"),
        texts_csv_edit=SimpleNamespace(text=lambda: "texts.csv"),
        translations_csv_edit=SimpleNamespace(text=lambda: "translations.csv"),
        out_dir_edit=SimpleNamespace(text=lambda: "out"),
        provider_combo=SimpleNamespace(currentText=lambda: "anthropic"),
        model_edit=SimpleNamespace(text=lambda: "model"),
        source_lang_edit=SimpleNamespace(text=lambda: "auto"),
        target_lang_edit=SimpleNamespace(text=lambda: "Vietnamese"),
        batch_size_spin=SimpleNamespace(value=lambda: 5),
        workers_spin=SimpleNamespace(value=lambda: 2),
        glossary_path_edit=SimpleNamespace(text=lambda: "terms.csv"),
        correction_table_path_edit=SimpleNamespace(text=lambda: "corrections.csv"),
        theme_mode="light",
        remember_api_check=SimpleNamespace(isChecked=lambda: False),
        log_api_check=SimpleNamespace(isChecked=lambda: False),
        reuse_memory_check=SimpleNamespace(isChecked=lambda: True),
        save_memory_check=SimpleNamespace(isChecked=lambda: True),
        preview_source_edit=SimpleNamespace(text=lambda: "preview-source.csv"),
        preview_output_edit=SimpleNamespace(text=lambda: "preview-output.csv"),
        preview_target_edit=SimpleNamespace(text=lambda: "Korean"),
        preview_provider_combo=SimpleNamespace(currentText=lambda: "openai"),
        preview_model_edit=SimpleNamespace(text=lambda: "preview-model"),
        preview_workers_spin=SimpleNamespace(value=lambda: 3),
        _log=lambda _msg: None,
    )

    ProviderTabMixin.save_settings(widget)

    assert saved[0]["preview_source"] == "preview-source.csv"
    assert saved[0]["preview_output"] == "preview-output.csv"
    assert saved[0]["preview_target_lang"] == "Korean"
    assert saved[0]["preview_provider"] == "openai"
    assert saved[0]["preview_model"] == "preview-model"
    assert saved[0]["preview_workers"] == 3


def test_finished_disables_preview_stop_button():
    _app()

    widget = SimpleNamespace()
    widget.current_worker = object()
    widget._set_running_ui = lambda _running: None
    widget.preview_stop_btn = SimpleNamespace(enabled=True, setEnabled=lambda value: setattr(widget.preview_stop_btn, "enabled", value))

    TranslatorGUI._on_finished(widget)

    assert widget.preview_stop_btn.enabled is False
