from __future__ import annotations

from collections.abc import Callable, Sequence
from pathlib import Path

import pytest

from game_llm_translator.llm import LLMProvider
from game_llm_translator.models import TextEntry, TranslationResult, text_identity


class _MockProvider(LLMProvider):
    def __init__(self, behaviors: Sequence[str | Exception | Callable[[str], str]]):
        self.behaviors = list(behaviors)
        self.calls: list[list[TextEntry]] = []

    def translate_batch(
        self,
        entries: list[TextEntry],
        target_lang: str,
        source_lang: str | None = None,
    ) -> list[TranslationResult]:
        self.calls.append(list(entries))
        if not self.behaviors:
            raise RuntimeError("mock exhausted")
        action = self.behaviors.pop(0)
        if isinstance(action, Exception):
            raise action
        if callable(action):
            return [TranslationResult(e.file, e.key, e.source, action(e.source), e.context) for e in entries]
        return [TranslationResult(e.file, e.key, e.source, str(action), e.context) for e in entries]


def test_mock_provider_basic_translates_each_entry():
    provider = _MockProvider([lambda s: f"VI:{s}"])
    entries = [
        TextEntry(Path("a.json"), "$.k1", "Hello"),
        TextEntry(Path("b.json"), "$.k2", "World"),
    ]
    results = provider.translate_batch(entries, "Vietnamese")
    assert [r.target for r in results] == ["VI:Hello", "VI:World"]
    assert len(provider.calls) == 1


def test_mock_provider_raises_on_demand():
    provider = _MockProvider([ValueError("simulated")])
    with pytest.raises(ValueError, match="simulated"):
        provider.translate_batch([TextEntry(Path("a.json"), "$.k", "x")], "Vietnamese")


def test_mock_provider_exhausted_raises_runtime_error():
    provider = _MockProvider([])
    with pytest.raises(RuntimeError, match="exhausted"):
        provider.translate_batch([TextEntry(Path("a.json"), "$.k", "x")], "Vietnamese")


def test_results_dedupe_uses_file_key_identity_across_batches():
    """Two entries with same key but different file MUST stay distinct."""
    provider = _MockProvider(["Ha-rôn", "Thuốc"])
    entry_a = TextEntry(Path("Actors.json"), "$[1].name", "Harold")
    entry_b = TextEntry(Path("Items.json"), "$[1].name", "Potion")

    res_a = provider.translate_batch([entry_a], "Vietnamese")
    res_b = provider.translate_batch([entry_b], "Vietnamese")

    by_id = {}
    for r in res_a + res_b:
        by_id[text_identity(r.file, r.key)] = r
    assert len(by_id) == 2
    assert by_id[text_identity(Path("Actors.json"), "$[1].name")].target == "Ha-rôn"
    assert by_id[text_identity(Path("Items.json"), "$[1].name")].target == "Thuốc"


def test_text_identity_distinguishes_same_key_different_files():
    a = text_identity(Path("Actors.json"), "$[1].name")
    b = text_identity(Path("Items.json"), "$[1].name")
    assert a != b
    assert a[1] == b[1]  # same key
    assert a[0] != b[0]  # different file


def test_provider_glossary_default_empty():
    provider = _MockProvider([])
    assert provider.glossary_block == ""


def test_provider_set_glossary_assigns_block():
    provider = _MockProvider([])
    provider.set_glossary("## Glossary\n- a -> b\n")
    assert "## Glossary" in provider.glossary_block


def test_provider_set_glossary_none_becomes_empty():
    provider = _MockProvider([])
    provider.set_glossary("")
    assert provider.glossary_block == ""


import csv
import threading
import time

from game_llm_translator.translate_pipeline import (
    STOPPED,
    build_char_batches,
    build_source_groups,
    dedupe_group_key,
    fanout_results,
    run_translate,
    TranslateOptions,
)


# ------------------------------------------------------------------
# build_char_batches tests
# ------------------------------------------------------------------

def test_build_char_batches_respects_max_entries():
    entries = [TextEntry(Path("f.json"), str(i), "x") for i in range(10)]
    batches = build_char_batches(entries, max_entries=3, max_chars=99999)
    assert all(len(b) <= 3 for b in batches)
    assert sum(len(b) for b in batches) == 10


def test_build_char_batches_respects_max_chars():
    entries = [TextEntry(Path("f.json"), str(i), "a" * 500) for i in range(6)]
    batches = build_char_batches(entries, max_entries=99, max_chars=1200)
    # each entry is 500 chars; 2 fit under 1200, 3 do not
    assert all(sum(len(e.source) for e in b) <= 1200 for b in batches)
    assert sum(len(b) for b in batches) == 6


def test_build_char_batches_single_oversized_entry_still_added():
    """An entry larger than max_chars must still be placed in its own batch."""
    entries = [TextEntry(Path("f.json"), "k", "a" * 5000)]
    batches = build_char_batches(entries, max_entries=10, max_chars=100)
    assert len(batches) == 1
    assert len(batches[0]) == 1


def test_build_char_batches_empty_input():
    assert build_char_batches([], max_entries=10) == []


# ------------------------------------------------------------------
# correction table integration tests
# ------------------------------------------------------------------

def test_run_translate_applies_correction_table(tmp_path):
    entries = [TextEntry(Path("a.json"), "$.k", "Hello")]
    out = tmp_path / "translations.csv"
    corrections_csv = tmp_path / "corrections.csv"
    corrections_csv.write_text("find,replace\nHi,Xin chào\n", encoding="utf-8")

    provider = _MockProvider([lambda s: "Hi"])
    options = TranslateOptions(
        target_lang="Vietnamese",
        provider="google",
        model="google",
        use_memory=False,
        save_memory=False,
        correction_table_path=corrections_csv,
    )

    import game_llm_translator.translate_pipeline as tp
    original = tp._make_provider
    tp._make_provider = lambda _o, _g="": provider
    try:
        results, _ = run_translate(entries, out, options)
    finally:
        tp._make_provider = original

    assert results[0].target == "Xin chào"


def test_run_translate_correction_table_applied_in_retry(tmp_path):
    """Correction table must also run on retry_sub recovered entries."""
    entries = [TextEntry(Path("a.json"), "$.k", "Hello")]
    out = tmp_path / "translations.csv"
    corrections_csv = tmp_path / "corrections.csv"
    corrections_csv.write_text("find,replace\nHi,Xin chào\n", encoding="utf-8")
    attempts: dict[str, int] = {}

    class RetryProvider(_MockProvider):
        def __init__(self):
            super().__init__([])

        def translate_batch(self, entries, target_lang, source_lang=None):
            key = entries[0].source
            attempts[key] = attempts.get(key, 0) + 1
            if attempts[key] == 1:
                raise ValueError("first attempt fails")
            return [TranslationResult(e.file, e.key, e.source, "Hi", e.context) for e in entries]

    options = TranslateOptions(
        target_lang="Vietnamese",
        provider="google",
        model="google",
        batch_size=1,
        use_memory=False,
        save_memory=False,
        correction_table_path=corrections_csv,
    )

    import game_llm_translator.translate_pipeline as tp
    original = tp._make_provider
    tp._make_provider = lambda _o, _g="": RetryProvider()
    try:
        results, report = run_translate(entries, out, options)
    finally:
        tp._make_provider = original

    assert results[0].target == "Xin chào"
    assert report.batches_failed == 1


# ------------------------------------------------------------------
# _make_provider glossary_block passthrough test
# ------------------------------------------------------------------

def test_make_provider_passes_glossary_block(tmp_path):
    """_make_provider with a pre-built glossary_block must set it on the provider."""
    from game_llm_translator.translate_pipeline import _make_provider
    options = TranslateOptions(
        target_lang="Vietnamese",
        provider="google",
        model="google",
    )
    provider = _MockProvider([])

    import game_llm_translator.translate_pipeline as tp
    original_make = tp.make_provider
    tp.make_provider = lambda *a, **kw: provider
    try:
        result = _make_provider(options, glossary_block="## Glossary\n- a -> b\n")
    finally:
        tp.make_provider = original_make

    assert "## Glossary" in result.glossary_block


def test_fanout_expands_unique_result_to_all_siblings():
    e1 = TextEntry(Path("Map001.json"), "$.k1", "Hello", context_text="ctx1")
    e2 = TextEntry(Path("Map002.json"), "$.k1", "Hello", context_text="ctx1")
    e3 = TextEntry(Path("Map003.json"), "$.k1", "Hello", context_text="ctx1")
    gkey = dedupe_group_key(e1)
    groups = {gkey: [e1, e2, e3]}
    rep_map = {text_identity(e1.file, e1.key): gkey}
    batch_results = [TranslationResult(Path("Map001.json"), "$.k1", "Hello", "Xin chào")]

    expanded = fanout_results(batch_results, groups, rep_map)

    assert len(expanded) == 3
    assert all(r.target == "Xin chào" for r in expanded)


def test_fanout_respects_different_context_text():
    e1 = TextEntry(Path("a.json"), "$.k", "Wait", context_text="ui")
    e2 = TextEntry(Path("b.json"), "$.k", "Wait", context_text="dialogue")
    groups = build_source_groups([e1, e2])
    assert len(groups) == 2


def test_run_translate_with_mock_provider(tmp_path):
    entries = [TextEntry(Path("a.json"), "$.k", "Hello")]
    out = tmp_path / "translations.csv"

    class Provider(_MockProvider):
        pass

    provider = _MockProvider([lambda s: f"VI:{s}"])

    options = TranslateOptions(target_lang="Vietnamese", provider="google", model="google", use_memory=False, save_memory=False)

    import game_llm_translator.translate_pipeline as tp

    original = tp._make_provider
    tp._make_provider = lambda _o, _g="": provider
    try:
        results, report = run_translate(entries, out, options)
    finally:
        tp._make_provider = original

    assert results[0].target == "VI:Hello"
    assert report.translated == 1
    assert out.exists()


def test_run_translate_stop_event_aborts(tmp_path):
    entries = [
        TextEntry(Path("a.json"), "$.k1", "one"),
        TextEntry(Path("a.json"), "$.k2", "two"),
        TextEntry(Path("a.json"), "$.k3", "three"),
    ]
    out = tmp_path / "translations.csv"
    stop = threading.Event()

    class SlowProvider(_MockProvider):
        def translate_batch(self, entries, target_lang, source_lang=None):
            self.calls.append(list(entries))
            stop.set()
            time.sleep(0.05)
            raise RuntimeError(STOPPED)

    provider = SlowProvider([lambda s: f"VI:{s}"])
    options = TranslateOptions(
        target_lang="Vietnamese",
        provider="google",
        model="google",
        batch_size=1,
        use_memory=False,
        save_memory=False,
        stop_event=stop,
    )

    import game_llm_translator.translate_pipeline as tp

    original = tp._make_provider
    tp._make_provider = lambda _o, _g="": provider
    try:
        with pytest.raises(RuntimeError, match="Stopped by user"):
            run_translate(entries, out, options)
    finally:
        tp._make_provider = original

    assert len(provider.calls) >= 1


def test_run_translate_parallel_uses_distinct_provider_instances_and_consistent_report(tmp_path):
    entries = [TextEntry(Path("a.json"), f"$.k{i}", f"src{i}") for i in range(6)]
    out = tmp_path / "translations.csv"
    providers: list[_MockProvider] = []
    lock = threading.Lock()

    class Provider(_MockProvider):
        def __init__(self):
            super().__init__([lambda s: f"VI:{s}"])

        def translate_batch(self, entries, target_lang, source_lang=None):
            time.sleep(0.01)
            return super().translate_batch(entries, target_lang, source_lang)

    options = TranslateOptions(
        target_lang="Vietnamese",
        provider="google",
        model="google",
        batch_size=1,
        workers=3,
        use_memory=False,
        save_memory=False,
    )

    import game_llm_translator.translate_pipeline as tp

    original = tp._make_provider

    def factory(_options, _glossary_block=""):
        provider = Provider()
        with lock:
            providers.append(provider)
        return provider

    tp._make_provider = factory
    try:
        results, report = run_translate(entries, out, options)
    finally:
        tp._make_provider = original

    assert sorted(r.target for r in results) == [f"VI:src{i}" for i in range(6)]
    assert report.translated == 6
    assert report.fallback == 0
    assert len(providers) == 6
    assert all(len(provider.calls) == 1 for provider in providers)


def test_run_translate_parallel_mixed_failure_recovers_and_counts_failed_batches(tmp_path):
    entries = [TextEntry(Path("a.json"), f"$.k{i}", f"src{i}") for i in range(3)]
    out = tmp_path / "translations.csv"
    attempts: dict[str, int] = {}
    attempts_lock = threading.Lock()

    class Provider(_MockProvider):
        def __init__(self):
            super().__init__([])

        def translate_batch(
            self,
            entries: list[TextEntry],
            target_lang: str,
            source_lang: str | None = None,
        ) -> list[TranslationResult]:
            batch = entries
            key = batch[0].source
            with attempts_lock:
                attempts[key] = attempts.get(key, 0) + 1
                attempt = attempts[key]
            if key == "src1" and attempt == 1:
                raise ValueError("non-retryable once")
            return [TranslationResult(e.file, e.key, e.source, f"VI:{e.source}", e.context) for e in batch]

    options = TranslateOptions(
        target_lang="Vietnamese",
        provider="google",
        model="google",
        batch_size=1,
        workers=2,
        use_memory=False,
        save_memory=False,
    )

    import game_llm_translator.translate_pipeline as tp

    original = tp._make_provider
    tp._make_provider = lambda _options, _g="": Provider()
    try:
        results, report = run_translate(entries, out, options)
    finally:
        tp._make_provider = original

    assert sorted(r.target for r in results) == ["VI:src0", "VI:src1", "VI:src2"]
    assert report.translated == 3
    assert report.fallback == 0
    assert report.batches_failed == 1
    assert attempts["src1"] == 2
