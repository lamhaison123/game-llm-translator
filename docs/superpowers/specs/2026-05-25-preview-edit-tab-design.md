# Preview & Edit Tab — Design Spec

**Date:** 2026-05-25
**Status:** Draft
**Scope:** Add a new "Preview & Edit" tab to the Game LLM Translator GUI

## Problem

Users currently translate via the Translate tab (CLI-like), then review via the Review tab (opens a separate dialog). The Review dialog (`TranslationEditor`) works but has limitations:

- **No live translation flow** — you must run translate elsewhere, then open the CSV
- **No warning indicators** — placeholder mismatches, overflow, newline issues are invisible until post-apply
- **No context filters** — cannot filter by "dialogue only" or "skill names"
- **No inline editing during translation** — must wait for the entire batch to finish before reviewing

## Solution

Add a new **Preview & Edit** tab that combines translation execution with real-time result display and inline editing.

## Architecture

### New files

- `src/game_llm_translator/gui/tabs/preview_tab.py` — PreviewTabMixin class

### Modified files

- `src/game_llm_translator/gui/main_window.py` — add PreviewTabMixin to class hierarchy, create tab
- `src/game_llm_translator/gui/tabs/__init__.py` — export new mixin

### No changes to

- `translate_pipeline.py` — reuses `run_translate` as-is via a thin wrapper
- `llm.py`, `csv_store.py`, `validate.py` — consumed as-is

## Tab Layout

```
┌────────────────────────────────────────────────────────┐
│ [Game] [Provider] [Preview & Edit] [Translate] [Apply] │
│                                                        │
│ ┌─ Config ──────────────────────────────────────────┐  │
│ │ Source CSV: [texts.csv      ] [Browse]             │  │
│ │ Output:     [translations.csv] [Browse]            │  │
│ │ Target Lang: [Vietnamese]    Provider: [anthropic] │  │
│ │ Model: [claude-sonnet-4-20250514]  Workers: [1]    │  │
│ │ Glossary: [terms.csv]  Correction: [ct.csv]        │  │
│ └────────────────────────────────────────────────────┘  │
│                                                        │
│ [▶ Start Translation] [■ Stop] [💾 Save CSV]          │
│ Progress: ████████░░░░░░ 142/890  Warnings: 23        │
│                                                        │
│ ┌─ Filters ─────────────────────────────────────────┐  │
│ │ [All] [Untranslated] [Warnings] Context: [All ▼]  │  │
│ │ Search: [____________________] [Go]                │  │
│ │ Showing 142/890                                     │  │
│ └────────────────────────────────────────────────────┘  │
│                                                        │
│ ┌──────────────────────────────────────────────────┐   │
│ │ #  │File     │Key       │Source    │Target     │S│   │
│ │ 1  │Map001   │$[1].2    │バカな…   │Không thể… │✓│   │
│ │ 2  │Map001   │$[1].3    │待って！  │Đợi đã!    │✓│   │
│ │ 3  │Actors   │$[1].name │フォル    │Foru       │⚠│   │
│ └──────────────────────────────────────────────────┘   │
│                                                        │
│ Source: バカな……この氷は……                              │
│ Target: [Không thể nào... băng này...             ]    │
│ ⚠ newline count mismatch (1 vs 0)                     │
│ [Save Row] [Use Source] [Next Warning ▶]              │
└────────────────────────────────────────────────────────┘
```

## Components

### 1. Config Section (top)

Reuses the same config fields as the Translate tab. Reads from `self.config` just like Translate/Provider tabs. Fields:

- Source CSV path (file picker)
- Output CSV path (file picker, defaults to `translations.csv` next to source)
- Target language, provider, model, workers
- Glossary path, correction table path

### 2. Progress Bar

Shows batch progress during translation. Uses `WorkerSignals.log` to track done/total.

### 3. Filter Bar

Four filter modes:
- **All** — show everything
- **Untranslated** — entries where `target == source` or `target` is empty
- **Warnings** — entries with validation warnings (from `validate.translation_warnings`)
- **Context dropdown** — populated from unique context values in the data (e.g., "dialogue", "skill name", "item description")

Plus a free-text search box that filters on source + target + key.

### 4. Table (QTreeWidget)

Columns: `#` | `File` | `Key` | `Source` | `Target` | `Status`

Status column shows:
- `✓` — translated (target differs from source)
- `⚠` — has warnings
- `○` — untranslated/fallback (target == source or empty)
- `→` — currently being translated (live indicator)

Fallback rows colored orange (same as existing ReviewTab behavior). Warning rows get a yellow background.

Clicking a row populates the source/target editors below.

### 5. Source/Target Editors

- **Source**: read-only `QPlainTextEdit`
- **Target**: editable `QPlainTextEdit`
- Below the target: warning text from `validate.translation_warnings(source, target, context)` displayed inline
- **Save Row** — writes edited target back to the in-memory data model
- **Use Source** — copies source text into target (for manual translation)
- **Next Warning** — jumps to the next row with warnings

### 6. Save CSV

Writes the current in-memory data to the output CSV path. Uses `csv_store.save_results` format.

## Data Flow

1. User selects source CSV and config
2. Clicks "Start Translation" → loads entries from CSV via `csv_store.load_entries`
3. Translation runs in a background thread using `translate_pipeline.run_translate`
4. As batches complete, results are emitted via signals and inserted into the table
5. User can filter, search, and edit target text at any time
6. "Save CSV" writes the current state to disk

### Live update mechanism

The existing `run_translate` processes entries in batches and calls `on_log` for progress. We add a new signal `WorkerSignals.result_batch(list[TranslationResult])` that emits each completed batch. The Preview tab connects this signal to update the table model in the main thread.

### Editing during translation

- Edits are applied to an in-memory list of `TranslationResult` objects
- If the user edits a row that's about to be overwritten by a later batch, the edit is preserved (user edits take priority over LLM results — check a "manually edited" flag per row)
- The "Save CSV" button writes all rows including edited ones

### Manual flag per row

Each row in the table tracks a `manually_edited: bool` flag. When the user edits the target field and clicks "Save Row", the flag is set to `True`. During translation, if a batch result would overwrite a manually-edited row, the manual edit is kept instead.

## Context Dropdown Population

After loading entries, scan all `context` fields and populate a `QComboBox` with unique values sorted alphabetically. "All" is the default. Context values use `_context_hint` labels (same as LLM prompt), e.g.:

- dialogue
- choice option
- skill name
- item description
- actor name
- etc.

## Warning Indicators

Use `validate.translation_warnings(source, target, context)` to compute warnings for each row. Display:

- Icon in status column (`⚠`)
- Warning text below the target editor
- Count in filter bar ("Warnings: 23")
- "Next Warning" button to jump through warning rows

## Integration Points

### With existing tabs

- **Translate tab** — unchanged, continues to work as-is
- **Review tab** — unchanged, still available for simple CSV review
- **Apply tab** — reads the saved translations.csv from Preview & Edit tab's output path

### With existing code

- `translate_pipeline.run_translate` — called in background thread
- `csv_store.load_entries` / `csv_store.save_results` — for CSV I/O
- `validate.translation_warnings` — for warning indicators
- `validate.check_noun_consistency` — optional: run after translation completes, show noun inconsistency warnings
- `WorkerSignals` — add `result_batch` signal for live updates

## Edge Cases

- **Empty CSV** — show "No entries found" message
- **Translation error** — show error in progress area, keep partial results
- **User stops mid-translation** — partial results remain in table, can still edit and save
- **Large files (5000+ entries)** — QTreeWidget may be slow; use batch insertion and limit visible rows if needed
- **Same source CSV opened twice** — reload data, discard previous edits (prompt to save first if dirty)

## Out of Scope

- Editing source, key, or context fields (target-only editing)
- Undo/redo history (future enhancement)
- Diff view comparing two versions (future enhancement)
- Auto-apply after save (user goes to Apply tab manually)
