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


# ---------------------------------------------------------------------------
# Pre-dedup fan-out logic (extracted from gui._translate_entries)
# ---------------------------------------------------------------------------


def _fanout(batch_results, source_groups):
    """Standalone copy of the fan-out logic for testing without Tk."""
    expanded = []
    for r in batch_results:
        siblings = source_groups.get(r.source, [])
        for e in siblings:
            expanded.append(TranslationResult(e.file, e.key, e.source, r.target, e.context))
        if not siblings:
            expanded.append(r)
    return expanded


def test_fanout_expands_unique_result_to_all_siblings():
    e1 = TextEntry(Path("Map001.json"), "$.k1", "Hello")
    e2 = TextEntry(Path("Map002.json"), "$.k1", "Hello")
    e3 = TextEntry(Path("Map003.json"), "$.k1", "Hello")
    source_groups = {"Hello": [e1, e2, e3]}
    batch_results = [TranslationResult(Path("Map001.json"), "$.k1", "Hello", "Xin chào")]

    expanded = _fanout(batch_results, source_groups)

    assert len(expanded) == 3
    assert {(r.file, r.key) for r in expanded} == {
        (Path("Map001.json"), "$.k1"),
        (Path("Map002.json"), "$.k1"),
        (Path("Map003.json"), "$.k1"),
    }
    assert all(r.target == "Xin chào" for r in expanded)


def test_fanout_handles_unknown_source_passthrough():
    """If a result's source isn't in source_groups, keep the result unchanged."""
    e = TextEntry(Path("a.json"), "$.k", "Hi")
    source_groups: dict = {"Hi": [e]}
    extra = TranslationResult(Path("z.json"), "$.x", "Surprise", "Bất ngờ")
    batch_results = [
        TranslationResult(Path("a.json"), "$.k", "Hi", "Chào"),
        extra,
    ]

    expanded = _fanout(batch_results, source_groups)

    assert len(expanded) == 2
    assert any(r.source == "Surprise" and r.target == "Bất ngờ" for r in expanded)


def test_fanout_empty_results_returns_empty():
    assert _fanout([], {"x": []}) == []


def test_source_groups_dedup_count():
    """Verify the dedup math used in _translate_entries."""
    entries = [
        TextEntry(Path("a.json"), "$.k1", "Hello"),
        TextEntry(Path("b.json"), "$.k1", "Hello"),
        TextEntry(Path("c.json"), "$.k1", "World"),
    ]
    groups: dict = {}
    for e in entries:
        groups.setdefault(e.source, []).append(e)
    unique = [g[0] for g in groups.values()]

    assert len(unique) == 2
    assert len(entries) - len(unique) == 1
    assert {u.source for u in unique} == {"Hello", "World"}
