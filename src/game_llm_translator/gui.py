from __future__ import annotations

from pathlib import Path
import concurrent.futures
import csv
import queue
import shutil
import threading
import time
import tkinter as tk
from tkinter import filedialog, messagebox, ttk
from typing import Callable

from .app_config import load_app_config, save_app_config
from .app_logging import log_event, logs_dir
from .auto import analyze_game, auto_translate_game, write_analysis_report
from .csv_store import load_entries, load_results, save_results
from .editor import open_file_editor
from .llm import make_provider
from .models import TextEntry, TranslationResult, text_identity
from .rpg_maker import apply_rpg_maker, engine_to_gui_game_type, extract_rpg_maker_mv, extract_rpg_maker_mz, normalize_gui_game_type
from .rpg_maker_cheat import apply_cheat, cheat_manifest_path, cheat_status, detect_cheat_engine, remove_cheat
from .translation_memory import global_memory_path, load_memory, save_memory
from .glossary import load_glossary, format_glossary_for_prompt


class TranslatorGUI(tk.Tk):
    def __init__(self) -> None:
        super().__init__()
        self.title("Game LLM Translator")
        self.geometry("1060x780")
        self.minsize(980, 680)
        self.events: queue.Queue[str] = queue.Queue()
        self.stop_requested = threading.Event()
        self.current_worker: threading.Thread | None = None
        self.action_buttons: list[ttk.Button] = []
        self.backup_paths: list[Path] = []
        self.translate_progress: dict[str, float | int] = {"done": 0, "total": 0, "started": 0.0}
        try:
            import sv_ttk
            sv_ttk.set_theme("light")
        except Exception:
            pass
        self._build()
        self.after(150, self._drain_events)

    def _build(self) -> None:
        root = ttk.Frame(self, padding=12)
        root.pack(fill=tk.BOTH, expand=True)

        config = load_app_config()
        self.game_type = tk.StringVar(value=normalize_gui_game_type(str(config.get("game_type", "rpg-maker-mv"))))
        self.game_dir = tk.StringVar(value=str(config.get("game_dir", "")))
        self.texts_csv = tk.StringVar(value=str(config.get("texts_csv", "work/texts.csv")))
        self.translations_csv = tk.StringVar(value=str(config.get("translations_csv", "work/translations.csv")))
        self.out_dir = tk.StringVar(value=str(config.get("out_dir", "work/translated")))
        self.provider = tk.StringVar(value=str(config.get("provider", "google")))
        self.model = tk.StringVar(value=str(config.get("model", "claude-opus-4-7")))
        self.api_key = tk.StringVar(value=str(config.get("api_key", "")))
        self.api_base = tk.StringVar(value=str(config.get("api_base", "")))
        self.source_lang = tk.StringVar(value=str(config.get("source_lang", "auto")))
        self.target_lang = tk.StringVar(value=str(config.get("target_lang", "Vietnamese")))
        self.batch_size = tk.IntVar(value=int(config.get("batch_size", 30)))
        self.workers = tk.IntVar(value=int(config.get("workers", 1)))
        self.glossary_path = tk.StringVar(value=str(config.get("glossary_path", "")))
        self.restart = tk.BooleanVar(value=False)
        self.remember_api_key = tk.BooleanVar(value=bool(config.get("remember_api_key", bool(config.get("api_key")))))
        self.reuse_memory = tk.BooleanVar(value=bool(config.get("reuse_memory", True)))
        self.save_memory_enabled = tk.BooleanVar(value=bool(config.get("save_memory", True)))
        self.status_text = tk.StringVar(value="Idle")
        self.scan_summary = tk.StringVar(value="Choose a game folder, then scan.")
        self.provider_note = tk.StringVar(value="")
        self.cheat_status_text = tk.StringVar(value="Cheat plugin: no game selected")

        self._build_header(root)
        self._build_tabs(root)
        self._build_status_bar(root)
        root.columnconfigure(0, weight=1)
        root.rowconfigure(1, weight=1)
        self._update_api_fields()
        self.refresh_backups()

    def _build_header(self, parent: ttk.Frame) -> None:
        header = ttk.Frame(parent)
        header.grid(row=0, column=0, sticky="ew", pady=(0, 8))
        ttk.Label(header, text="Game LLM Translator", font=("Segoe UI", 16, "bold")).pack(side=tk.LEFT)
        ttk.Label(header, textvariable=self.status_text).pack(side=tk.RIGHT)

    def _build_tabs(self, parent: ttk.Frame) -> None:
        notebook = ttk.Notebook(parent)
        notebook.grid(row=1, column=0, sticky="nsew")
        parent.rowconfigure(1, weight=1)
        self._build_game_tab(notebook)
        self._build_provider_tab(notebook)
        self._build_translate_tab(notebook)
        self._build_review_tab(notebook)
        self._build_apply_tab(notebook)
        self._build_recovery_tab(notebook)
        self._build_logs_tab(notebook)

    def _build_game_tab(self, notebook: ttk.Notebook) -> None:
        tab = ttk.Frame(notebook, padding=12)
        notebook.add(tab, text="Game")
        self._path_row(tab, 0, "Game folder", self.game_dir, self._choose_game_dir)
        self._row(tab, 1, "Game type", ttk.Combobox(tab, textvariable=self.game_type, values=["rpg-maker-mv", "rpg-maker-mz"], state="readonly"))
        self._readonly_row(tab, 2, "Texts CSV", self.texts_csv)
        self._readonly_row(tab, 3, "Translations CSV", self.translations_csv)
        self._readonly_row(tab, 4, "Output folder", self.out_dir)
        self._action_button(tab, "Scan Game", self.scan).grid(row=5, column=1, sticky="w", pady=10)
        ttk.Label(tab, textvariable=self.scan_summary, wraplength=760, justify=tk.LEFT).grid(row=6, column=0, columnspan=3, sticky="ew", pady=6)
        ttk.Label(tab, text="Tip: choose the RPG Maker MV/MZ game folder that contains Game.exe or www/data.", foreground="#555").grid(row=7, column=0, columnspan=3, sticky="w", pady=6)
        tab.columnconfigure(1, weight=1)

    def _build_provider_tab(self, notebook: ttk.Notebook) -> None:
        tab = ttk.Frame(notebook, padding=12)
        notebook.add(tab, text="Provider")
        provider_box = ttk.Combobox(tab, textvariable=self.provider, values=["google", "mymemory", "libretranslate", "bing", "yandex", "anthropic", "openai", "openai-compatible"], state="readonly")
        provider_box.bind("<<ComboboxSelected>>", lambda _event: self._update_api_fields())
        self._row(tab, 0, "Provider", provider_box)
        self.model_entry = ttk.Entry(tab, textvariable=self.model)
        self._row(tab, 1, "Model", self.model_entry)
        self.api_key_label = ttk.Label(tab, text="API key")
        self.api_key_label.grid(row=2, column=0, sticky="w", pady=3)
        self.api_key_entry = ttk.Entry(tab, textvariable=self.api_key, show="*")
        self.api_key_entry.grid(row=2, column=1, columnspan=2, sticky="ew", pady=3)
        self.api_base_label = ttk.Label(tab, text="API base")
        self.api_base_label.grid(row=3, column=0, sticky="w", pady=3)
        self.api_base_entry = ttk.Entry(tab, textvariable=self.api_base)
        self.api_base_entry.grid(row=3, column=1, columnspan=2, sticky="ew", pady=3)
        self._row(tab, 4, "Source language", ttk.Entry(tab, textvariable=self.source_lang))
        self._row(tab, 5, "Target language", ttk.Entry(tab, textvariable=self.target_lang))
        ttk.Checkbutton(tab, text="Remember API key/base on this computer (plaintext config file)", variable=self.remember_api_key).grid(row=6, column=1, columnspan=2, sticky="w", pady=8)
        ttk.Label(tab, text="API keys are stored as plaintext only when the checkbox above is enabled.", foreground="#8a5a00", wraplength=720).grid(row=7, column=1, columnspan=2, sticky="w")
        ttk.Label(tab, textvariable=self.provider_note, wraplength=760, justify=tk.LEFT, foreground="#555").grid(row=8, column=1, columnspan=2, sticky="ew", pady=8)
        self._action_button(tab, "Save Settings", self.save_settings).grid(row=9, column=1, sticky="w", pady=10)
        tab.columnconfigure(1, weight=1)

    def _build_translate_tab(self, notebook: ttk.Notebook) -> None:
        tab = ttk.Frame(notebook, padding=12)
        notebook.add(tab, text="Translate")
        ttk.Label(tab, text="Safe workflow: Extract text -> Start/Resume translation -> Review -> Export copy.", wraplength=760).grid(row=0, column=0, columnspan=3, sticky="w", pady=(0, 10))
        self._action_button(tab, "Extract Text", self.extract).grid(row=1, column=0, sticky="w", padx=(0, 8), pady=4)
        self._action_button(tab, "Start / Resume Translation", self.translate).grid(row=1, column=1, sticky="w", padx=8, pady=4)
        self._action_button(tab, "Extract + Translate + Export Copy", self.pipeline).grid(row=1, column=2, sticky="w", padx=8, pady=4)
        advanced = ttk.LabelFrame(tab, text="Advanced translation options", padding=10)
        advanced.grid(row=2, column=0, columnspan=3, sticky="ew", pady=14)
        self._row(advanced, 0, "Batch size (0 = auto)", ttk.Spinbox(advanced, from_=0, to=200, textvariable=self.batch_size))
        self._row(advanced, 1, "Workers (parallel batches)", ttk.Spinbox(advanced, from_=1, to=8, textvariable=self.workers))
        ttk.Label(advanced, text="Glossary CSV (optional)").grid(row=2, column=0, sticky="w", padx=4, pady=3)
        glossary_frame = ttk.Frame(advanced)
        glossary_frame.grid(row=2, column=1, sticky="ew", pady=3)
        ttk.Entry(glossary_frame, textvariable=self.glossary_path).pack(side=tk.LEFT, fill=tk.X, expand=True)
        ttk.Button(glossary_frame, text="Browse", command=self._choose_glossary).pack(side=tk.LEFT, padx=(4, 0))
        ttk.Checkbutton(advanced, text="Ignore existing translations and start over", variable=self.restart).grid(row=3, column=1, sticky="w", pady=3)
        ttk.Checkbutton(advanced, text="Reuse translation memory", variable=self.reuse_memory).grid(row=4, column=1, sticky="w", pady=3)
        ttk.Checkbutton(advanced, text="Save successful translations to memory", variable=self.save_memory_enabled).grid(row=5, column=1, sticky="w", pady=3)
        ttk.Label(advanced, text="Glossary CSV columns: term, translation, [note]. Terms here will be translated EXACTLY as listed in every batch.", foreground="#555", wraplength=720).grid(row=6, column=1, columnspan=2, sticky="w", pady=4)
        ttk.Label(advanced, text="If old translations include asset filenames from an older parser run, use Backups > Clear Old Translation before translating again.", foreground="#555", wraplength=720).grid(row=7, column=1, columnspan=2, sticky="w", pady=4)
        advanced.columnconfigure(1, weight=1)
        tab.columnconfigure(2, weight=1)

    def _build_review_tab(self, notebook: ttk.Notebook) -> None:
        tab = ttk.Frame(notebook, padding=12)
        notebook.add(tab, text="Review")
        ttk.Label(tab, text="Review or manually edit translations before exporting/applying them.").grid(row=0, column=0, columnspan=3, sticky="w", pady=(0, 10))
        self._action_button(tab, "Review/Edit Translations", self.edit_table).grid(row=1, column=0, sticky="w", padx=(0, 8))
        self._action_button(tab, "Open CSV Externally", self.edit_csv).grid(row=1, column=1, sticky="w", padx=8)
        files = ttk.LabelFrame(tab, text="Advanced file locations", padding=10)
        files.grid(row=2, column=0, columnspan=3, sticky="ew", pady=14)
        self._path_row(files, 0, "Texts CSV", self.texts_csv, lambda: self._choose_save(self.texts_csv))
        self._path_row(files, 1, "Translations CSV", self.translations_csv, lambda: self._choose_save(self.translations_csv))
        self._path_row(files, 2, "Output folder", self.out_dir, lambda: self._choose_dir(self.out_dir))
        files.columnconfigure(1, weight=1)
        tab.columnconfigure(2, weight=1)

    def _build_apply_tab(self, notebook: ttk.Notebook) -> None:
        tab = ttk.Frame(notebook, padding=12)
        notebook.add(tab, text="Export / Apply")
        safe = ttk.LabelFrame(tab, text="Safe export", padding=10)
        safe.grid(row=0, column=0, sticky="nsew", padx=(0, 8), pady=4)
        ttk.Label(safe, text="Writes translated JSON to the output folder only. Does not change the game.", wraplength=430).grid(row=0, column=0, sticky="w", pady=(0, 8))
        self._action_button(safe, "Export Translated Data", self.export_translated_data).grid(row=1, column=0, sticky="w")
        risky = ttk.LabelFrame(tab, text="Apply to game folder", padding=10)
        risky.grid(row=0, column=1, sticky="nsew", padx=(8, 0), pady=4)
        ttk.Label(risky, text="Creates a backup, then replaces JSON files in the game data folder. Close the game first.", wraplength=430, foreground="#8a0000").grid(row=0, column=0, sticky="w", pady=(0, 8))
        self._action_button(risky, "Apply to Game...", self.apply_to_game).grid(row=1, column=0, sticky="w")
        cheat = ttk.LabelFrame(tab, text="Cheat plugin", padding=10)
        cheat.grid(row=1, column=0, columnspan=2, sticky="ew", pady=(12, 0))
        ttk.Label(cheat, text="Installs RPG Maker MV/MZ Cheat UI Plugin from GitHub. Toggle in game: Ctrl+C. Remove uses this app's manifest only.", wraplength=900).grid(row=0, column=0, columnspan=3, sticky="w", pady=(0, 8))
        ttk.Label(cheat, textvariable=self.cheat_status_text, foreground="#555", wraplength=900).grid(row=1, column=0, columnspan=3, sticky="w", pady=(0, 8))
        self._action_button(cheat, "Apply Cheat...", self.apply_cheat_plugin).grid(row=2, column=0, sticky="w", padx=(0, 8))
        self._action_button(cheat, "Remove Cheat", self.remove_cheat_plugin).grid(row=2, column=1, sticky="w", padx=8)
        self._action_button(cheat, "Refresh Cheat Status", self.refresh_cheat_status).grid(row=2, column=2, sticky="w", padx=8)
        tab.columnconfigure(0, weight=1)
        tab.columnconfigure(1, weight=1)
        tab.rowconfigure(0, weight=1)

    def _build_recovery_tab(self, notebook: ttk.Notebook) -> None:
        tab = ttk.Frame(notebook, padding=12)
        notebook.add(tab, text="Backups")
        buttons = ttk.Frame(tab)
        buttons.grid(row=0, column=0, sticky="ew", pady=(0, 8))
        self._action_button(buttons, "Refresh Backups", self.refresh_backups).pack(side=tk.LEFT, padx=4)
        self._action_button(buttons, "Create Backup Now", self.create_backup_now).pack(side=tk.LEFT, padx=4)
        self._action_button(buttons, "Open Selected Backup", self.open_selected_backup).pack(side=tk.LEFT, padx=4)
        self._action_button(buttons, "Restore Selected Backup", self.restore_backup).pack(side=tk.LEFT, padx=4)
        self._action_button(buttons, "Delete Selected Backup(s)", self.delete_selected_backups).pack(side=tk.LEFT, padx=4)
        self._action_button(buttons, "Delete All Backups", self.delete_all_backups).pack(side=tk.LEFT, padx=4)
        self._action_button(buttons, "Clear Old Translation", self.clear_old_translation).pack(side=tk.RIGHT, padx=4)
        self._action_button(buttons, "Clear Game Memory", self.clear_game_memory).pack(side=tk.RIGHT, padx=4)
        self._action_button(buttons, "Clear Global Memory", self.clear_global_memory).pack(side=tk.RIGHT, padx=4)
        columns = ("kind", "path")
        self.backups_tree = ttk.Treeview(tab, columns=columns, show="headings", height=12, selectmode="extended")
        self.backups_tree.heading("kind", text="Backup type")
        self.backups_tree.heading("path", text="Path")
        self.backups_tree.column("kind", width=150, anchor=tk.W)
        self.backups_tree.column("path", width=760, anchor=tk.W)
        self.backups_tree.grid(row=1, column=0, sticky="nsew")
        ttk.Label(tab, text="Restore creates data_before_restore_* first. Delete only removes listed backup folders; game data is not changed.", foreground="#555").grid(row=2, column=0, sticky="w", pady=8)
        tab.columnconfigure(0, weight=1)
        tab.rowconfigure(1, weight=1)

    def _build_logs_tab(self, notebook: ttk.Notebook) -> None:
        tab = ttk.Frame(notebook, padding=12)
        notebook.add(tab, text="Logs")
        buttons = ttk.Frame(tab)
        buttons.grid(row=0, column=0, sticky="ew", pady=(0, 8))
        self._action_button(buttons, "Open Log Folder", self.open_logs).pack(side=tk.LEFT, padx=4)
        ttk.Button(buttons, text="Clear View", command=lambda: self.log.delete("1.0", tk.END)).pack(side=tk.LEFT, padx=4)
        self.log = tk.Text(tab, height=18, wrap=tk.WORD)
        self.log.grid(row=1, column=0, sticky="nsew")
        tab.columnconfigure(0, weight=1)
        tab.rowconfigure(1, weight=1)

    def _build_status_bar(self, parent: ttk.Frame) -> None:
        bar = ttk.Frame(parent)
        bar.grid(row=2, column=0, sticky="ew", pady=(8, 0))
        ttk.Label(bar, textvariable=self.status_text).pack(side=tk.LEFT)
        self.progress = ttk.Progressbar(bar, mode="indeterminate", length=260)
        self.progress.pack(side=tk.LEFT, padx=12)
        self.progress_text = tk.StringVar(value="")
        ttk.Label(bar, textvariable=self.progress_text, foreground="#555").pack(side=tk.LEFT, padx=4)
        self.stop_button = ttk.Button(bar, text="Stop", command=self.stop_current, state="disabled")
        self.stop_button.pack(side=tk.RIGHT)

    def _action_button(self, parent: tk.Widget, text: str, command: Callable[[], None]) -> ttk.Button:
        button = ttk.Button(parent, text=text, command=command)
        self.action_buttons.append(button)
        return button

    def _provider_uses_api_key(self) -> bool:
        return self.provider.get() in {"anthropic", "openai", "openai-compatible", "libretranslate", "bing", "yandex"}

    def _provider_uses_api_base(self) -> bool:
        return self.provider.get() in {"openai-compatible", "libretranslate"}

    def _update_api_fields(self) -> None:
        provider = self.provider.get()
        key_state = "normal" if self._provider_uses_api_key() else "disabled"
        base_state = "normal" if self._provider_uses_api_base() else "disabled"
        model_state = "normal" if provider in {"anthropic", "openai", "openai-compatible"} else "disabled"
        self.api_key_entry.configure(state=key_state)
        self.api_base_entry.configure(state=base_state)
        self.model_entry.configure(state=model_state)
        labels = {
            "anthropic": "Anthropic API key",
            "openai": "OpenAI API key",
            "openai-compatible": "OpenAI-compatible API key",
            "libretranslate": "LibreTranslate API key (optional)",
            "bing": "Microsoft Translator key",
            "yandex": "Yandex API key",
        }
        self.api_key_label.configure(text=labels.get(provider, "API key not required"))
        self.api_base_label.configure(text="Base URL" if provider in {"openai-compatible", "libretranslate"} else "API base not used")
        notes = {
            "google": "No API key required. MTL output may be rough; review translations before applying.",
            "mymemory": "No API key required. Public service may rate-limit requests.",
            "libretranslate": "Set LibreTranslate URL/API key here if using a custom server; otherwise environment defaults may be used.",
            "bing": "Microsoft Translator may require key/region depending on your account setup.",
            "yandex": "Yandex may require API key and folder/project configuration.",
            "anthropic": "Uses model/context-aware batches. Keep API keys private.",
            "openai": "Uses OpenAI chat completions. Keep API keys private.",
            "openai-compatible": "Requires a compatible base URL and model name from your provider/local server.",
        }
        self.provider_note.set(notes.get(provider, ""))

    def save_settings(self) -> None:
        data = {
            "game_type": self.game_type.get(),
            "game_dir": self.game_dir.get(),
            "texts_csv": self.texts_csv.get(),
            "translations_csv": self.translations_csv.get(),
            "out_dir": self.out_dir.get(),
            "provider": self.provider.get(),
            "model": self.model.get(),
            "source_lang": self.source_lang.get(),
            "target_lang": self.target_lang.get(),
            "batch_size": int(self.batch_size.get()),
            "workers": int(self.workers.get()),
            "glossary_path": self.glossary_path.get(),
            "remember_api_key": self.remember_api_key.get(),
            "reuse_memory": self.reuse_memory.get(),
            "save_memory": self.save_memory_enabled.get(),
        }
        if self.remember_api_key.get():
            data["api_key"] = self.api_key.get()
            data["api_base"] = self.api_base.get()
        save_app_config(data)
        if self.remember_api_key.get():
            self._log("Settings saved. API key/base are stored as plaintext on this machine.")
        else:
            self._log("Settings saved without API key/base.")

    def _check_stopped(self) -> None:
        if self.stop_requested.is_set():
            raise RuntimeError("Stopped by user")

    def stop_current(self) -> None:
        if self.current_worker and self.current_worker.is_alive():
            self.stop_requested.set()
            self._log("Stop requested. Waiting for current safe checkpoint...")

    def _ui_call(self, func: Callable[[], None]) -> None:
        self.after(0, func)

    def _set_running_ui(self, running: bool, name: str = "") -> None:
        state = "disabled" if running else "normal"
        for button in self.action_buttons:
            button.configure(state=state)
        self.stop_button.configure(state="normal" if running else "disabled")
        self.status_text.set(f"Running: {name}" if running else "Idle")
        if running:
            self.translate_progress = {"done": 0, "total": 0, "started": time.monotonic()}
            self.progress.configure(mode="indeterminate")
            self.progress.start(10)
            self.progress_text.set("")
        else:
            self.progress.stop()
            self.progress.configure(mode="indeterminate", value=0)
            self.progress_text.set("")

    def _update_translate_progress(self, done: int, total: int) -> None:
        self.translate_progress["done"] = done
        self.translate_progress["total"] = total
        if total <= 0:
            return
        if self.progress.cget("mode") != "determinate":
            self.progress.stop()
            self.progress.configure(mode="determinate", maximum=total)
        self.progress.configure(value=done)
        elapsed = time.monotonic() - float(self.translate_progress["started"] or time.monotonic())
        if done > 0 and elapsed > 0:
            rate = done / elapsed
            remaining = (total - done) / rate if rate > 0 else 0
            mins = int(remaining // 60)
            secs = int(remaining % 60)
            pct = int(done * 100 / total)
            self.progress_text.set(f"{pct}% | {done}/{total} | ~{mins}m{secs:02d}s left")
        else:
            self.progress_text.set(f"{done}/{total}")

    def _row(self, parent: ttk.Frame, row: int, label: str, widget: tk.Widget) -> None:
        ttk.Label(parent, text=label).grid(row=row, column=0, sticky="w", pady=3)
        widget.grid(row=row, column=1, columnspan=2, sticky="ew", pady=3)

    def _readonly_row(self, parent: ttk.Frame, row: int, label: str, var: tk.StringVar) -> None:
        entry = ttk.Entry(parent, textvariable=var, state="readonly")
        self._row(parent, row, label, entry)

    def _path_row(self, parent: ttk.Frame, row: int, label: str, var: tk.StringVar, command) -> None:
        ttk.Label(parent, text=label).grid(row=row, column=0, sticky="w", pady=3)
        ttk.Entry(parent, textvariable=var).grid(row=row, column=1, sticky="ew", pady=3)
        ttk.Button(parent, text="Browse", command=command).grid(row=row, column=2, sticky="e", padx=4, pady=3)

    def _choose_game_dir(self) -> None:
        value = filedialog.askdirectory()
        if value:
            self.game_dir.set(value)
            self._set_default_work_paths(Path(value))
            self.refresh_backups()
            self.refresh_cheat_status()

    def _set_default_work_paths(self, game_dir: Path) -> None:
        work_dir = game_dir / "translator_work"
        self.texts_csv.set(str(work_dir / "texts.csv"))
        self.translations_csv.set(str(work_dir / "translations.csv"))
        self.out_dir.set(str(work_dir / "translated_data"))

    def _choose_dir(self, var: tk.StringVar) -> None:
        value = filedialog.askdirectory()
        if value:
            var.set(value)

    def _choose_save(self, var: tk.StringVar) -> None:
        value = filedialog.asksaveasfilename(defaultextension=".csv", filetypes=[("CSV", "*.csv"), ("All files", "*.*")])
        if value:
            var.set(value)

    def _choose_glossary(self) -> None:
        value = filedialog.askopenfilename(filetypes=[("CSV", "*.csv"), ("All files", "*.*")])
        if value:
            self.glossary_path.set(value)

    def _log(self, message: str) -> None:
        log_event(message)
        self.events.put(message)

    def open_logs(self) -> None:
        path = logs_dir()
        path.mkdir(parents=True, exist_ok=True)
        open_file_editor(path)

    def _drain_events(self) -> None:
        while not self.events.empty():
            self.log.insert(tk.END, self.events.get() + "\n")
            self.log.see(tk.END)
        self.after(150, self._drain_events)

    def _run(self, name: str, func) -> None:
        if self.current_worker and self.current_worker.is_alive():
            messagebox.showwarning(name, "Another task is already running")
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
                    self.after(0, lambda: messagebox.showerror(name, str(exc)))
            except Exception as exc:
                self._log(f"Error in {name}: {exc}")
                self.after(0, lambda: messagebox.showerror(name, str(exc)))
            finally:
                self.current_worker = None
                self.after(0, lambda: self._set_running_ui(False))
        self.current_worker = threading.Thread(target=worker, daemon=True)
        self.current_worker.start()

    def _game_dir_path(self) -> Path:
        value = self.game_dir.get().strip()
        if not value:
            raise ValueError("Choose a game folder first")
        path = Path(value)
        if not path.exists() or not path.is_dir():
            raise ValueError(f"Game folder not found: {path}")
        return path

    def _game_data_dir(self, game_dir: Path) -> Path:
        data_dir = game_dir / "www" / "data"
        if data_dir.exists():
            return data_dir
        data_dir = game_dir / "data"
        if data_dir.exists():
            return data_dir
        raise ValueError(f"RPG Maker data folder not found in: {game_dir}")

    def _backup_dirs(self, game_dir: Path) -> list[Path]:
        backups = [path for pattern in ("data_backup_*", "data_before_restore_*") for path in game_dir.glob(pattern) if path.is_dir()]
        return sorted(backups, key=lambda path: path.stat().st_mtime, reverse=True)

    def _selected_backup_dir(self) -> Path:
        selection = self.backups_tree.selection()
        if not selection:
            raise ValueError("Select a backup first")
        return self.backup_paths[int(selection[0])]

    def _selected_backup_dirs(self) -> list[Path]:
        selection = self.backups_tree.selection()
        if not selection:
            raise ValueError("Select one or more backups first")
        return [self.backup_paths[int(item)] for item in selection]

    def _backup_preview(self, paths: list[Path]) -> str:
        preview = "\n".join(str(path) for path in paths[:12])
        if len(paths) > 12:
            preview += f"\n...and {len(paths) - 12} more"
        return preview

    def _copy_json_files(self, source_dir: Path, target_dir: Path) -> int:
        files = list(source_dir.glob("*.json"))
        if not files:
            raise ValueError(f"No JSON files found in: {source_dir}")
        for file in files:
            self._check_stopped()
            shutil.copy2(file, target_dir / file.name)
        return len(files)

    def _extract_entries(self):
        game_dir = self._game_dir_path()
        if normalize_gui_game_type(self.game_type.get()) == "rpg-maker-mz":
            return extract_rpg_maker_mz(game_dir)
        return extract_rpg_maker_mv(game_dir)

    def extract(self) -> None:
        def job() -> None:
            from .csv_store import save_entries
            self._check_stopped()
            entries = self._extract_entries()
            self._check_stopped()
            save_entries(entries, Path(self.texts_csv.get()))
            self._log(f"Extracted {len(entries)} entries -> {self.texts_csv.get()}")
        self._run("extract text", job)

    def scan(self) -> None:
        def job() -> None:
            game_dir = self._game_dir_path()
            self._check_stopped()
            self._ui_call(lambda: self._set_default_work_paths(game_dir))
            report = analyze_game(game_dir, self.provider.get(), self.target_lang.get())
            self._check_stopped()
            if report["engine"] in {"mv", "mz", "mv-mz"}:
                selected_type = engine_to_gui_game_type(str(report["engine"]))
                self._ui_call(lambda: self.game_type.set(selected_type))
            write_analysis_report(report, game_dir / "translator_work" / "analysis.json")
            summary = f"Engine: {report['engine']} | JSON files: {report['json_files']} | Text entries: {report['text_entries']} | Data folder: {report['data_dir']}"
            self._ui_call(lambda: self.scan_summary.set(summary))
            self._log(summary)
            self._log(f"Contexts: {report['contexts']}")
            self._log(f"Analysis: {game_dir / 'translator_work' / 'analysis.json'}")
            self._ui_call(self.refresh_backups)
            self._ui_call(self.refresh_cheat_status)
        self._run("scan game", job)

    def _dedupe_results(self, results: list[TranslationResult], wanted_ids: set[tuple[str, str]]) -> list[TranslationResult]:
        by_id: dict[tuple[str, str], TranslationResult] = {}
        for result in results:
            identity = text_identity(result.file, result.key)
            if identity in wanted_ids:
                by_id[identity] = result
        return list(by_id.values())

    @staticmethod
    def _estimate_batch_size(entries: list[TextEntry], target_tokens: int = 8000) -> int:
        if not entries:
            return 30
        sample = entries[:min(20, len(entries))]
        avg_chars = sum(len(e.source) + len(e.context_text) for e in sample) / len(sample)
        avg_tokens = max(1, avg_chars / 3.5)
        size = max(1, int(target_tokens / avg_tokens))
        return min(size, 60)

    def _translate_entries(self, entries: list[TextEntry], translations_csv: Path) -> list[TranslationResult]:
        provider = make_provider(self.provider.get(), self.model.get(), self.api_key.get().strip() or None, self.api_base.get().strip() or None)
        glossary_path_str = self.glossary_path.get().strip()
        if glossary_path_str:
            glossary_entries = load_glossary(Path(glossary_path_str))
            if glossary_entries:
                provider.set_glossary(format_glossary_for_prompt(glossary_entries))
                self._log(f"Glossary: {len(glossary_entries)} entries loaded from {glossary_path_str}")
            else:
                self._log(f"Glossary: no entries loaded (file missing or empty): {glossary_path_str}")
        existing = [] if self.restart.get() else (load_results(translations_csv) if translations_csv.exists() else [])
        wanted_ids = {text_identity(entry.file, entry.key) for entry in entries}
        results = self._dedupe_results(existing, wanted_ids)
        completed = {text_identity(result.file, result.key) for result in results if result.target.strip() and result.target != result.source}
        memory = {result.source: result.target for result in results if result.source.strip() and result.target.strip() and result.target != result.source}
        work_memory = translations_csv.parent / "translation_memory.csv"
        persistent_memory: dict[str, str] = {}
        if self.reuse_memory.get():
            persistent_memory = load_memory([global_memory_path(), work_memory], self.target_lang.get(), None if self.source_lang.get().lower() == "auto" else self.source_lang.get())
            memory.update(persistent_memory)
        if persistent_memory:
            self._log(f"Loaded {len(persistent_memory)} memory entries")
        pending = [entry for entry in entries if text_identity(entry.file, entry.key) not in completed]
        to_translate: list[TextEntry] = []
        reused = 0
        for entry in pending:
            if entry.source in memory:
                results.append(TranslationResult(entry.file, entry.key, entry.source, memory[entry.source], entry.context))
                reused += 1
            else:
                to_translate.append(entry)
        if reused:
            results = self._dedupe_results(results, wanted_ids)
            save_results(results, translations_csv)
            self._log(f"Reused {reused} translations from memory")
        raw_size = int(self.batch_size.get())
        size = self._estimate_batch_size(to_translate) if raw_size == 0 else raw_size
        if raw_size == 0:
            self._log(f"Auto batch size: {size} entries/batch")
        num_workers = max(1, min(8, int(self.workers.get())))
        source = None if self.source_lang.get().lower() == "auto" else self.source_lang.get()
        target_lang = self.target_lang.get()
        save_memory_enabled = self.save_memory_enabled.get()
        provider_name = self.provider.get()
        batches = [to_translate[s:s + size] for s in range(0, len(to_translate), size)]
        results_lock = threading.Lock()
        translated_count = len(results)  # already done before this loop

        def _is_retryable(exc: Exception) -> tuple[bool, float]:
            """Return (retryable, suggested_delay_seconds)."""
            msg = str(exc)
            # Cloudflare 524 / generic with explicit retry_after (Python dict or JSON format)
            if "retry_after" in msg:
                import re as _re
                match = _re.search(r"['\"]retry_after['\"]\s*:\s*(\d+(?:\.\d+)?)", msg)
                if match:
                    return True, float(match.group(1))
            if "524" in msg:
                return True, 60.0
            # 429 rate limit
            if "429" in msg:
                return True, 10.0
            # 503 / 502 / 500 server errors
            if any(code in msg for code in ("503", "502", "500")):
                return True, 5.0
            # timeout keywords
            if any(kw in msg.lower() for kw in ("timeout", "timed out", "connection")):
                return True, 15.0
            return False, 0.0

        def run_batch(batch: list[TextEntry]) -> list[TranslationResult]:
            max_retries = 3
            for attempt in range(max_retries + 1):
                if self.stop_requested.is_set():
                    raise RuntimeError("Stopped by user")
                try:
                    return provider.translate_batch(batch, target_lang, source)
                except RuntimeError:
                    raise
                except Exception as exc:
                    retryable, suggested = _is_retryable(exc)
                    if retryable and attempt < max_retries:
                        delay = max(suggested, 5.0 * (attempt + 1))
                        self._log(f"Batch error (retry {attempt + 1}/{max_retries} in {delay:.0f}s): {exc}")
                        end = time.monotonic() + delay
                        while time.monotonic() < end:
                            if self.stop_requested.is_set():
                                raise RuntimeError("Stopped by user")
                            time.sleep(0.5)
                    else:
                        raise

        executor = concurrent.futures.ThreadPoolExecutor(max_workers=num_workers)
        futures: dict[concurrent.futures.Future, list[TextEntry]] = {
            executor.submit(run_batch, batch): batch for batch in batches
        }
        failed_batches: list[list[TextEntry]] = []
        try:
            for future in concurrent.futures.as_completed(futures):
                if self.stop_requested.is_set():
                    for f in futures:
                        f.cancel()
                    raise RuntimeError("Stopped by user")
                batch = futures[future]
                try:
                    batch_results = future.result()
                except RuntimeError:
                    raise
                except Exception as exc:
                    self._log(f"Batch failed after retries (will retry at end): {exc}")
                    failed_batches.append(batch)
                    with results_lock:
                        translated_count += len(batch)
                        self._log(f"Translated {min(translated_count, len(entries))}/{len(entries)} ({len(failed_batches)} batch(es) deferred)")
                        self._ui_call(lambda d=min(translated_count, len(entries)), t=len(entries): self._update_translate_progress(d, t))
                    continue
                with results_lock:
                    translated_count += len(batch)
                    results.extend(batch_results)
                    results = self._dedupe_results(results, wanted_ids)
                    save_results(results, translations_csv)
                    if save_memory_enabled:
                        saved_memory = save_memory(work_memory, batch_results, target_lang, source, provider_name)
                        save_memory(global_memory_path(), batch_results, target_lang, source, provider_name)
                        if saved_memory:
                            self._log(f"Saved {saved_memory} translations to memory")
                    self._log(f"Translated {min(translated_count, len(entries))}/{len(entries)}")
                    self._ui_call(lambda d=min(translated_count, len(entries)), t=len(entries): self._update_translate_progress(d, t))
        finally:
            executor.shutdown(wait=False)

        # Final retry pass for failed batches: serial, longer delays, smaller batches
        if failed_batches and not self.stop_requested.is_set():
            self._log(f"--- Retrying {len(failed_batches)} failed batch(es) with longer delays ---")
            time.sleep(min(60.0, 5.0))  # short cooldown before retry pass
            still_failed: list[list[TextEntry]] = []
            for batch in failed_batches:
                if self.stop_requested.is_set():
                    still_failed.append(batch)
                    continue
                # Split larger batches in half to reduce per-request load
                sub_batches = [batch] if len(batch) <= 10 else [batch[:len(batch)//2], batch[len(batch)//2:]]
                for sub in sub_batches:
                    if self.stop_requested.is_set():
                        still_failed.append(sub)
                        continue
                    try:
                        sub_results = run_batch(sub)
                        with results_lock:
                            results.extend(sub_results)
                            results = self._dedupe_results(results, wanted_ids)
                            save_results(results, translations_csv)
                            if save_memory_enabled:
                                save_memory(work_memory, sub_results, target_lang, source, provider_name)
                                save_memory(global_memory_path(), sub_results, target_lang, source, provider_name)
                            self._log(f"Recovered {len(sub_results)} entries from deferred batch")
                    except Exception as exc:
                        self._log(f"Deferred batch still failed (keeping source): {exc}")
                        still_failed.append(sub)
            # Fallback to source for batches that still failed
            for batch in still_failed:
                with results_lock:
                    fallback_results = [TranslationResult(e.file, e.key, e.source, e.source, e.context) for e in batch]
                    results.extend(fallback_results)
                    results = self._dedupe_results(results, wanted_ids)
                    save_results(results, translations_csv)
        # summary report
        total = len(entries)
        translated = sum(1 for r in results if r.target.strip() and r.target != r.source)
        fallback = total - translated
        from_memory = reused
        self._log(f"--- Translation report: {translated}/{total} translated, {fallback} fallback to source, {from_memory} from memory ---")
        return results

    def translate(self) -> None:
        def job() -> None:
            entries = load_entries(Path(self.texts_csv.get()))
            self._translate_entries(entries, Path(self.translations_csv.get()))
        self._run("translate", job)

    def edit_table(self) -> None:
        path = Path(self.translations_csv.get())
        if not path.exists():
            messagebox.showwarning("Review/Edit Translations", f"File not found: {path}")
            return
        TranslationEditor(self, path)

    def edit_csv(self) -> None:
        path = Path(self.translations_csv.get())
        if not path.exists():
            messagebox.showwarning("Open CSV Externally", f"File not found: {path}")
            return
        open_file_editor(path)

    def _export_results(self) -> Path:
        results = load_results(Path(self.translations_csv.get()))
        out_dir = Path(self.out_dir.get())
        apply_rpg_maker(results, out_dir)
        self._log(f"Exported translated RPG Maker JSON -> {out_dir}")
        return out_dir

    def export_translated_data(self) -> None:
        def job() -> None:
            self._export_results()
        self._run("export translated data", job)

    def apply(self) -> None:
        self.export_translated_data()

    def apply_to_game(self) -> None:
        try:
            game_dir = self._game_dir_path()
            data_dir = self._game_data_dir(game_dir)
            out_dir = Path(self.out_dir.get())
            backup_dir = game_dir / f"data_backup_{time.strftime('%Y%m%d_%H%M%S')}"
            translated_count = len(list(out_dir.glob("*.json"))) if out_dir.exists() else 0
        except Exception as exc:
            messagebox.showerror("Apply to Game", str(exc))
            return
        ok = messagebox.askyesno(
            "Apply to Game",
            f"This will export translations, create a backup, then replace game JSON files.\n\nTranslated output: {out_dir}\nGame data folder: {data_dir}\nBackup to create: {backup_dir}\nCurrently exported JSON files: {translated_count}\n\nClose the game before continuing. Continue?",
        )
        if not ok:
            self._log("Apply to game cancelled.")
            return

        def job() -> None:
            out = self._export_results()
            self._check_stopped()
            files = list(out.rglob("*.json"))
            if not files:
                raise ValueError(f"No translated JSON files found in: {out}")
            shutil.copytree(data_dir, backup_dir)
            self._log(f"Backup created -> {backup_dir}")
            self._check_stopped()
            for file in files:
                self._check_stopped()
                destination = data_dir / file.relative_to(out)
                destination.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(file, destination)
            self._log(f"Applied {len(files)} translated files -> {data_dir}")
            self._ui_call(self.refresh_backups)
        self._run("apply to game", job)

    def refresh_cheat_status(self) -> None:
        value = self.game_dir.get().strip()
        if not value:
            self.cheat_status_text.set("Cheat plugin: no game selected")
            return
        game_dir = Path(value)
        if not game_dir.exists():
            self.cheat_status_text.set("Cheat plugin: game folder not found")
            return
        status = cheat_status(game_dir)
        if status.error:
            self.cheat_status_text.set(f"Cheat plugin: manifest unreadable ({status.error}). Manifest: {status.manifest_path}")
            self._log(f"Cheat manifest status error: {status.error}")
        elif status.installed:
            detail = "OK"
            if status.missing_count or status.modified_count:
                detail = f"partial: {status.missing_count} missing, {status.modified_count} modified"
            self.cheat_status_text.set(f"Cheat plugin: installed ({status.engine}, {status.release_tag}, {status.file_count} files, {detail}). Manifest: {status.manifest_path}")
        else:
            self.cheat_status_text.set(f"Cheat plugin: not installed. Manifest: {status.manifest_path}")

    def apply_cheat_plugin(self) -> None:
        try:
            game_dir = self._game_dir_path()
            engine = detect_cheat_engine(game_dir, self.game_type.get())
            manifest_path = cheat_manifest_path(game_dir)
            destination = game_dir / "www" if engine == "mv" else game_dir
        except Exception as exc:
            messagebox.showerror("Apply Cheat", str(exc))
            return
        ok = messagebox.askyesno(
            "Apply Cheat",
            f"Download and install third-party cheat plugin code from GitHub into this game folder? The game will execute this plugin when launched.\n\nSource: https://github.com/paramonos/RPG-Maker-MV-MZ-Cheat-UI-Plugin\nEngine: {engine.upper()}\nDestination: {destination}\nManifest: {manifest_path}\n\nOverwritten files will be backed up. Cheat UI toggle in game: Ctrl+C. Continue?",
        )
        if not ok:
            self._log("Apply cheat cancelled.")
            return

        def job() -> None:
            manifest = apply_cheat(game_dir, engine, progress=lambda message: (self._check_stopped(), self._log(message))[1])
            self._log(f"Applied cheat plugin {manifest.release_tag} ({len(manifest.files)} files)")
            self._ui_call(self.refresh_cheat_status)
        self._run("apply cheat", job)

    def remove_cheat_plugin(self) -> None:
        try:
            game_dir = self._game_dir_path()
            manifest_path = cheat_manifest_path(game_dir)
        except Exception as exc:
            messagebox.showerror("Remove Cheat", str(exc))
            return
        if not manifest_path.exists():
            messagebox.showerror("Remove Cheat", f"No cheat install manifest found:\n{manifest_path}")
            return
        ok = messagebox.askyesno(
            "Remove Cheat",
            f"Remove cheat plugin using manifest only?\n\nManifest: {manifest_path}\n\nOnly files tracked by this app will be restored/removed. Continue?",
        )
        if not ok:
            self._log("Remove cheat cancelled.")
            return

        def job() -> None:
            manifest = remove_cheat(game_dir, progress=lambda message: (self._check_stopped(), self._log(message))[1])
            self._log(f"Removed cheat plugin manifest ({len(manifest.files)} tracked files)")
            self._ui_call(self.refresh_cheat_status)
        self._run("remove cheat", job)

    def clear_old_translation(self) -> None:
        targets = [Path(self.texts_csv.get()), Path(self.translations_csv.get()), Path(self.out_dir.get())]
        game_dir_value = self.game_dir.get().strip()
        if game_dir_value:
            targets.append(Path(game_dir_value) / "translator_work" / "translation_memory.csv")
        existing = [path for path in targets if path.exists()]
        if not existing:
            messagebox.showinfo("Clear Old Translation", "No old translation files found.")
            return
        preview = "\n".join(str(path) for path in existing[:12])
        if len(existing) > 12:
            preview += f"\n...and {len(existing) - 12} more"
        ok = messagebox.askyesno("Clear Old Translation", f"Delete generated translation files?\n\n{preview}\n\nGame backups and global translation memory will not be deleted.")
        if not ok:
            self._log("Clear old translation cancelled.")
            return

        def job() -> None:
            for path in existing:
                self._check_stopped()
                if path.is_dir():
                    shutil.rmtree(path)
                else:
                    path.unlink()
                self._log(f"Deleted old translation output -> {path}")
            self._ui_call(lambda: self.restart.set(True))
            self._log("Old translation outputs cleared. Start-over mode is now enabled.")
        self._run("clear old translation", job)

    def clear_game_memory(self) -> None:
        game_dir_value = self.game_dir.get().strip()
        if not game_dir_value:
            messagebox.showwarning("Clear Game Memory", "No game folder selected.")
            return
        path = Path(game_dir_value) / "translator_work" / "translation_memory.csv"
        if not path.exists():
            messagebox.showinfo("Clear Game Memory", f"No game memory file found:\n{path}")
            return
        ok = messagebox.askyesno("Clear Game Memory", f"Delete per-game translation memory?\n\n{path}\n\nGlobal memory will not be affected.\n\nNote: if a translate job is currently running, it may recreate this file as it saves new results.")
        if not ok:
            return
        try:
            path.unlink()
            self._log(f"Deleted game memory -> {path}")
            messagebox.showinfo("Clear Game Memory", f"Deleted:\n{path}")
        except Exception as exc:
            messagebox.showerror("Clear Game Memory", f"Failed to delete: {exc}")

    def clear_global_memory(self) -> None:
        path = global_memory_path()
        if not path.exists():
            messagebox.showinfo("Clear Global Memory", f"No global memory file found:\n{path}")
            return
        try:
            import csv as _csv
            with open(path, encoding="utf-8-sig") as f:
                count = sum(1 for _ in _csv.reader(f)) - 1
        except Exception:
            count = -1
        count_str = f"{count} entries" if count >= 0 else "unknown entries"
        ok = messagebox.askyesno(
            "Clear Global Memory",
            f"Delete global translation memory ({count_str})?\n\n{path}\n\nThis affects ALL games. This cannot be undone.\n\nNote: if a translate job is currently running, it may recreate this file as it saves new results. Stop translate first if you want to fully clear.",
        )
        if not ok:
            return
        try:
            path.unlink()
            self._log(f"Deleted global memory ({count_str}) -> {path}")
            messagebox.showinfo("Clear Global Memory", f"Deleted ({count_str}):\n{path}")
        except Exception as exc:
            messagebox.showerror("Clear Global Memory", f"Failed to delete: {exc}")

    def refresh_backups(self) -> None:
        if not hasattr(self, "backups_tree"):
            return
        for item in self.backups_tree.get_children():
            self.backups_tree.delete(item)
        self.backup_paths = []
        value = self.game_dir.get().strip()
        if not value:
            return
        game_dir = Path(value)
        if not game_dir.exists():
            return
        self.backup_paths = self._backup_dirs(game_dir)
        for index, path in enumerate(self.backup_paths):
            kind = "Before restore" if path.name.startswith("data_before_restore_") else "Game backup"
            self.backups_tree.insert("", tk.END, iid=str(index), values=(kind, str(path)))

    def create_backup_now(self) -> None:
        try:
            game_dir = self._game_dir_path()
            data_dir = self._game_data_dir(game_dir)
            backup_dir = game_dir / f"data_backup_{time.strftime('%Y%m%d_%H%M%S')}"
        except Exception as exc:
            messagebox.showerror("Create Backup", str(exc))
            return
        ok = messagebox.askyesno("Create Backup", f"Create backup now?\n\nFrom: {data_dir}\nTo: {backup_dir}")
        if not ok:
            return

        def job() -> None:
            self._check_stopped()
            shutil.copytree(data_dir, backup_dir)
            self._log(f"Backup created -> {backup_dir}")
            self._ui_call(self.refresh_backups)
        self._run("create backup", job)

    def open_selected_backup(self) -> None:
        try:
            open_file_editor(self._selected_backup_dir())
        except Exception as exc:
            messagebox.showerror("Open Selected Backup", str(exc))

    def _delete_backup_paths(self, game_dir: Path, paths: list[Path]) -> None:
        def job() -> None:
            allowed = {path.resolve() for path in self._backup_dirs(game_dir)}
            deleted = 0
            skipped = 0
            for path in paths:
                self._check_stopped()
                if path.resolve() not in allowed or not path.exists():
                    skipped += 1
                    self._log(f"Skipped backup delete -> {path}")
                    continue
                shutil.rmtree(path)
                deleted += 1
                self._log(f"Deleted backup -> {path}")
            self._log(f"Deleted {deleted} backup(s), skipped {skipped}.")
            self._ui_call(self.refresh_backups)
        self._run("delete backups", job)

    def delete_selected_backups(self) -> None:
        try:
            game_dir = self._game_dir_path()
            paths = self._selected_backup_dirs()
        except Exception as exc:
            messagebox.showerror("Delete Backups", str(exc))
            return
        ok = messagebox.askyesno(
            "Delete Backups",
            f"Permanently delete {len(paths)} selected backup folder(s)?\n\n{self._backup_preview(paths)}\n\nGame data is not changed. Continue?",
        )
        if not ok:
            self._log("Delete selected backups cancelled.")
            return
        self._delete_backup_paths(game_dir, paths)

    def delete_all_backups(self) -> None:
        try:
            game_dir = self._game_dir_path()
            paths = self._backup_dirs(game_dir)
        except Exception as exc:
            messagebox.showerror("Delete All Backups", str(exc))
            return
        if not paths:
            messagebox.showinfo("Delete All Backups", "No backup folders found.")
            return
        ok = messagebox.askyesno(
            "Delete All Backups",
            f"Permanently delete all {len(paths)} backup folder(s)?\n\nThis includes data_backup_* and data_before_restore_* folders.\n\n{self._backup_preview(paths)}\n\nGame data is not changed. Continue?",
        )
        if not ok:
            self._log("Delete all backups cancelled.")
            return
        self._delete_backup_paths(game_dir, paths)

    def restore_backup(self) -> None:
        try:
            game_dir = self._game_dir_path()
            backup_dir = self._selected_backup_dir()
            data_dir = self._game_data_dir(game_dir)
        except Exception as exc:
            messagebox.showerror("Restore Backup", str(exc))
            return
        ok = messagebox.askyesno("Restore Backup", f"Restore selected backup?\n\nFrom: {backup_dir}\nTo: {data_dir}\n\nA safety copy data_before_restore_* will be created first.")
        if not ok:
            self._log("Restore backup cancelled.")
            return

        def job() -> None:
            restore_safety = game_dir / f"data_before_restore_{time.strftime('%Y%m%d_%H%M%S')}"
            self._check_stopped()
            shutil.copytree(data_dir, restore_safety)
            self._log(f"Safety copy created -> {restore_safety}")
            self._check_stopped()
            copied = self._copy_json_files(backup_dir, data_dir)
            self._log(f"Restored {copied} files from {backup_dir} -> {data_dir}")
            self._ui_call(self.refresh_backups)
        self._run("restore backup", job)

    def auto_translate(self) -> None:
        def job() -> None:
            source = None if self.source_lang.get().lower() == "auto" else self.source_lang.get()
            game_dir = self._game_dir_path()
            self._check_stopped()
            self._ui_call(lambda: self._set_default_work_paths(game_dir))
            out = auto_translate_game(
                game_dir=game_dir,
                target_lang=self.target_lang.get(),
                source_lang=source,
                provider=self.provider.get(),
                model=self.model.get() or self.provider.get(),
                api_key=self.api_key.get().strip() or None,
                api_base=self.api_base.get().strip() or None,
                batch_size=int(self.batch_size.get()),
                work_dir=game_dir / "translator_work",
                in_place=False,
                restart=self.restart.get(),
                use_memory=self.reuse_memory.get(),
                progress=lambda message: (self._check_stopped(), self._log(message))[1],
            )
            self._check_stopped()
            self._ui_call(lambda: self.out_dir.set(str(out)))
            self._log(f"Auto translated and exported -> {out}")
        self._run("auto translate export", job)

    def pipeline(self) -> None:
        def job() -> None:
            from .csv_store import save_entries
            entries = self._extract_entries()
            self._check_stopped()
            save_entries(entries, Path(self.texts_csv.get()))
            self._log(f"Extracted {len(entries)} entries -> {self.texts_csv.get()}")
            results = self._translate_entries(entries, Path(self.translations_csv.get()))
            self._check_stopped()
            out_dir = Path(self.out_dir.get())
            apply_rpg_maker(results, out_dir)
            self._log(f"Extracted, translated, and exported RPG Maker copy -> {out_dir}")
        self._run("extract translate export", job)


class TranslationEditor(tk.Toplevel):
    def __init__(self, parent: TranslatorGUI, path: Path) -> None:
        super().__init__(parent)
        self.path = path
        self.title(f"Review/Edit translations - {path.name}")
        self.geometry("1100x720")
        self.rows: list[dict[str, str]] = []
        self.filtered_indices: list[int] = []
        self.current_index: int | None = None
        self._filter_var = tk.StringVar(value="all")
        self._search_var = tk.StringVar()
        self._build()
        self._load()

    def _build(self) -> None:
        main = ttk.Frame(self, padding=10)
        main.pack(fill=tk.BOTH, expand=True)

        # filter bar
        filter_bar = ttk.Frame(main)
        filter_bar.grid(row=0, column=0, columnspan=4, sticky="ew", pady=(0, 6))
        ttk.Label(filter_bar, text="Filter:").pack(side=tk.LEFT, padx=(0, 4))
        for label, value in [("All", "all"), ("Untranslated / Fallback", "fallback"), ("Translated", "translated")]:
            ttk.Radiobutton(filter_bar, text=label, variable=self._filter_var, value=value, command=self._apply_filter).pack(side=tk.LEFT, padx=4)
        ttk.Label(filter_bar, text="Search:").pack(side=tk.LEFT, padx=(16, 4))
        search_entry = ttk.Entry(filter_bar, textvariable=self._search_var, width=24)
        search_entry.pack(side=tk.LEFT, padx=4)
        search_entry.bind("<Return>", lambda _: self._apply_filter())
        ttk.Button(filter_bar, text="Go", command=self._apply_filter).pack(side=tk.LEFT, padx=2)
        self.count_label = ttk.Label(filter_bar, text="")
        self.count_label.pack(side=tk.RIGHT, padx=8)

        columns = ("index", "file", "key", "source", "target")
        self.tree = ttk.Treeview(main, columns=columns, show="headings", height=16, selectmode="browse")
        widths = {"index": 60, "file": 200, "key": 160, "source": 280, "target": 280}
        for column in columns:
            self.tree.heading(column, text=column)
            self.tree.column(column, width=widths[column], anchor=tk.W)
        self.tree.grid(row=1, column=0, columnspan=3, sticky="nsew")
        self.tree.bind("<<TreeviewSelect>>", self._on_select)

        scroll = ttk.Scrollbar(main, orient=tk.VERTICAL, command=self.tree.yview)
        self.tree.configure(yscrollcommand=scroll.set)
        scroll.grid(row=1, column=3, sticky="ns")

        ttk.Label(main, text="Source").grid(row=2, column=0, sticky="w", pady=(10, 2))
        ttk.Label(main, text="Target").grid(row=2, column=1, sticky="w", pady=(10, 2))
        self.source_box = tk.Text(main, height=6, wrap=tk.WORD)
        self.target_box = tk.Text(main, height=6, wrap=tk.WORD)
        self.source_box.grid(row=3, column=0, sticky="nsew", padx=(0, 6))
        self.target_box.grid(row=3, column=1, columnspan=2, sticky="nsew")

        buttons = ttk.Frame(main)
        buttons.grid(row=4, column=0, columnspan=3, sticky="ew", pady=8)
        ttk.Button(buttons, text="Save Current Row", command=self._save_current).pack(side=tk.LEFT, padx=4)
        ttk.Button(buttons, text="Save CSV", command=self._save_file).pack(side=tk.LEFT, padx=4)
        ttk.Button(buttons, text="Use Source as Translation", command=self._copy_source).pack(side=tk.LEFT, padx=4)
        ttk.Button(buttons, text="Close", command=self.destroy).pack(side=tk.RIGHT, padx=4)

        main.columnconfigure(0, weight=1)
        main.columnconfigure(1, weight=1)
        main.columnconfigure(2, weight=1)
        main.rowconfigure(1, weight=3)
        main.rowconfigure(3, weight=1)

    def _load(self) -> None:
        with self.path.open("r", newline="", encoding="utf-8-sig") as fp:
            self.rows = [dict(row) for row in csv.DictReader(fp)]
        self._apply_filter()
        if self.filtered_indices:
            first_iid = str(self.filtered_indices[0])
            self.tree.selection_set(first_iid)
            self.tree.see(first_iid)

    def _is_fallback(self, row: dict[str, str]) -> bool:
        src = row.get("source", "").strip()
        tgt = row.get("target", "").strip()
        return not tgt or tgt == src

    def _apply_filter(self) -> None:
        self._save_current(update_tree=False)
        mode = self._filter_var.get()
        search = self._search_var.get().lower()
        for item in self.tree.get_children():
            self.tree.delete(item)
        self.filtered_indices = []
        for index, row in enumerate(self.rows):
            if mode == "fallback" and not self._is_fallback(row):
                continue
            if mode == "translated" and self._is_fallback(row):
                continue
            if search and search not in (row.get("source", "") + row.get("target", "") + row.get("key", "")).lower():
                continue
            self.filtered_indices.append(index)
            tag = "fallback" if self._is_fallback(row) else ""
            self.tree.insert("", tk.END, iid=str(index), values=(index, row.get("file", ""), row.get("key", ""), row.get("source", ""), row.get("target", "")), tags=(tag,))
        self.tree.tag_configure("fallback", foreground="#cc4400")
        fallback_count = sum(1 for r in self.rows if self._is_fallback(r))
        self.count_label.configure(text=f"Showing {len(self.filtered_indices)}/{len(self.rows)} | Fallback: {fallback_count}")
        self.current_index = None

    def _on_select(self, _event=None) -> None:
        selection = self.tree.selection()
        if not selection:
            return
        if self.current_index is not None:
            self._save_current(update_tree=True)
        self.current_index = int(selection[0])
        row = self.rows[self.current_index]
        self.source_box.delete("1.0", tk.END)
        self.source_box.insert("1.0", row.get("source", ""))
        self.target_box.delete("1.0", tk.END)
        self.target_box.insert("1.0", row.get("target", ""))

    def _save_current(self, update_tree: bool = True) -> None:
        if self.current_index is None:
            return
        row = self.rows[self.current_index]
        row["target"] = self.target_box.get("1.0", tk.END).rstrip("\n")
        if update_tree and self.tree.exists(str(self.current_index)):
            tag = "fallback" if self._is_fallback(row) else ""
            self.tree.item(str(self.current_index), values=(self.current_index, row.get("file", ""), row.get("key", ""), row.get("source", ""), row.get("target", "")), tags=(tag,))

    def _copy_source(self) -> None:
        self.target_box.delete("1.0", tk.END)
        self.target_box.insert("1.0", self.source_box.get("1.0", tk.END).rstrip("\n"))
        self._save_current()

    def _save_file(self) -> None:
        self._save_current()
        fieldnames = ["file", "key", "source", "target", "context"]
        with self.path.open("w", newline="", encoding="utf-8") as fp:
            writer = csv.DictWriter(fp, fieldnames=fieldnames)
            writer.writeheader()
            for row in self.rows:
                writer.writerow({name: row.get(name, "") for name in fieldnames})
        messagebox.showinfo("Save CSV", f"Saved {self.path}")


def main() -> None:
    TranslatorGUI().mainloop()
