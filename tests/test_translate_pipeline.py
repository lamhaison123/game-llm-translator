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
