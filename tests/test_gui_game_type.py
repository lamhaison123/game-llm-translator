from game_llm_translator.rpg_maker_common import (
    engine_to_gui_game_type,
    gui_game_type_to_engine,
    normalize_gui_game_type,
)


def test_normalize_gui_game_type_preserves_unity_xunity():
    assert normalize_gui_game_type("unity-xunity") == "unity-xunity"
    assert normalize_gui_game_type("xunity") == "unity-xunity"


def test_engine_to_gui_game_type_handles_unity_xunity():
    assert engine_to_gui_game_type("unity-xunity") == "unity-xunity"


def test_gui_game_type_to_engine_handles_unity_xunity():
    assert gui_game_type_to_engine("unity-xunity") == "unity-xunity"
