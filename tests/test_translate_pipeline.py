from __future__ import annotations

from pathlib import Path

import pytest

from game_llm_translator.llm import LLMProvider
from game_llm_translator.models import TextEntry, TranslationResult, text_identity


class _MockProvider(LLMProvider):
    def __init__(self, behaviors):
        self.behaviors = list(behaviors)
        self.calls: list[list[TextEntry]] = []

    def translate_batch(self, entries, target_lang, source_lang=None):
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


from game_llm_translator.translate_pipeline import (
    build_source_groups,
    dedupe_group_key,
    fanout_results,
    run_translate,
    TranslateOptions,
)


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
    tp._make_provider = lambda _o: provider
    try:
        results, report = run_translate(entries, out, options)
    finally:
        tp._make_provider = original

    assert results[0].target == "VI:Hello"
    assert report.translated == 1
    assert out.exists()
