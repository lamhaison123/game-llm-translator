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


def test_engine_to_gui_game_type_mv_mz_maps_to_mz():
    assert engine_to_gui_game_type("mv-mz") == "rpg-maker-mz"


def test_engine_to_gui_game_type_mz():
    assert engine_to_gui_game_type("mz") == "rpg-maker-mz"


def test_engine_to_gui_game_type_mv():
    assert engine_to_gui_game_type("mv") == "rpg-maker-mv"


def test_gui_game_type_to_engine_handles_unity_xunity():
    assert gui_game_type_to_engine("unity-xunity") == "unity-xunity"


def test_gui_game_type_to_engine_rpg_maker_mz():
    assert gui_game_type_to_engine("rpg-maker-mz") == "mz"


def test_gui_game_type_to_engine_rpg_maker_mv():
    assert gui_game_type_to_engine("rpg-maker-mv") == "mv"


def test_gui_game_type_to_engine_fallback_is_mv():
    assert gui_game_type_to_engine(None) == "mv"
    assert gui_game_type_to_engine("unknown") == "mv"
