from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from PySide6.QtWidgets import QApplication

from game_llm_translator.csv_store import save_results
from game_llm_translator.gui.tabs.preview_tab import PreviewRow, PreviewTabMixin
from game_llm_translator.models import TranslationResult


def _app():
    app = QApplication.instance() or QApplication([])
    return app


def test_preview_load_replaces_current_selection_and_editor_contents(tmp_path):
    _app()

    first_csv = tmp_path / "first.csv"
    second_csv = tmp_path / "second.csv"
    save_results([TranslationResult(Path("first.json"), "$.a", "First source", "First target")], first_csv)
    save_results([TranslationResult(Path("second.json"), "$.b", "Second source", "Second target")], second_csv)

    widget = SimpleNamespace()
    widget.preview_source_edit = SimpleNamespace(text=lambda: str(second_csv))
    widget.preview_output_edit = SimpleNamespace(text=lambda: "")
    widget._preview_dirty = False
    widget._preview_current_idx = 0
    widget._preview_rows = [PreviewRow(0, "first.json", "$.a", "First source", "First target", "")]
    widget.preview_target_box = SimpleNamespace(toPlainText=lambda: "Edited stale target")
    widget._preview_populate_contexts = lambda: None
    widget._preview_apply_filter = lambda: None
    widget._preview_update_progress = lambda: None
    widget.preview_tree = SimpleNamespace(clear=lambda: None, selectedItems=lambda: [])
    widget.preview_source_box = SimpleNamespace(clear=lambda: None)
    widget.preview_target_box = SimpleNamespace(clear=lambda: None, toPlainText=lambda: "")
    widget.preview_warning_label = SimpleNamespace(clear=lambda: None)

    PreviewTabMixin._preview_load(widget)

    assert widget._preview_current_idx is None
    assert [row.source for row in widget._preview_rows] == ["Second source"]
    assert [row.target for row in widget._preview_rows] == ["Second target"]


def test_preview_apply_filter_updates_count_label():
    _app()

    widget = SimpleNamespace()
    widget._preview_current_idx = None
    widget._preview_rows = [PreviewRow(0, "file.json", "$.a", "Source", "Target", "")]
    widget._preview_save_current = lambda update_tree=True: None
    widget.preview_filter_group = SimpleNamespace(
        buttons=lambda: [SimpleNamespace(isChecked=lambda: True, property=lambda _name: "all")]
    )
    widget.preview_context_combo = SimpleNamespace(currentText=lambda: "All")
    widget.preview_search_edit = SimpleNamespace(text=lambda: "")
    widget.preview_tree = SimpleNamespace(clear=lambda: None, addTopLevelItem=lambda _item: None, palette=lambda: None)
    widget._preview_add_tree_item = lambda _row: None
    widget.preview_count_label = SimpleNamespace(text="", setText=lambda value: setattr(widget.preview_count_label, "text", value))

    PreviewTabMixin._preview_apply_filter(widget)

    assert widget.preview_count_label.text == "Showing 1/1"


def test_preview_load_initializes_entry_mapping_for_later_batches(tmp_path):
    _app()

    csv_file = tmp_path / "translations.csv"
    save_results([TranslationResult(Path("file.json"), "$.a", "Source", "Target")], csv_file)

    widget = SimpleNamespace()
    widget.preview_source_edit = SimpleNamespace(text=lambda: str(csv_file))
    widget.preview_output_edit = SimpleNamespace(text=lambda: "")
    widget._preview_dirty = False
    widget._preview_current_idx = None
    widget.preview_tree = SimpleNamespace(clear=lambda: None)
    widget.preview_source_box = SimpleNamespace(clear=lambda: None)
    widget.preview_target_box = SimpleNamespace(clear=lambda: None)
    widget.preview_warning_label = SimpleNamespace(clear=lambda: None)
    widget._preview_populate_contexts = lambda: None
    widget._preview_apply_filter = lambda: None
    widget._preview_update_progress = lambda: None
    widget._preview_update_tree_item = lambda _row: None

    PreviewTabMixin._preview_load(widget)
    PreviewTabMixin._preview_on_batch(widget, [TranslationResult(Path("file.json"), "$.a", "Source", "Updated")])

    assert widget._preview_rows[0].target == "Updated"


def test_preview_save_preserves_sub_keys(tmp_path, monkeypatch):
    _app()

    output_csv = tmp_path / "translations.csv"
    widget = SimpleNamespace()
    widget._preview_current_idx = None
    widget.preview_output_edit = SimpleNamespace(text=lambda: str(output_csv))
    widget._preview_rows = [PreviewRow(0, "Map001.json", "$.events[1]", "A\nB", "Một\nHai", "", ["$.a", "$.b"])]
    widget._preview_save_current = lambda update_tree=True: None

    from PySide6.QtWidgets import QMessageBox
    monkeypatch.setattr(QMessageBox, "information", lambda *_args, **_kwargs: None)

    PreviewTabMixin._preview_save(widget)

    from game_llm_translator.csv_store import load_results
    assert load_results(output_csv)[0].sub_keys == ["$.a", "$.b"]
