"""Auto-replace game fonts with CJK+Vietnamese-capable font when target language uses Vietnamese glyphs.

RPG Maker MV/MZ games often ship with fonts that only contain CJK + basic Latin
(e.g. SourceHanSansTC, MS Gothic, GameFont). When translated to Vietnamese, many
characters (đ, ư, ơ, ê, ô, ă, â) render as blank boxes or tofu.

This module detects the situation and replaces the game's main font with
Noto Sans CJK JP (bundled in vendor/fonts/), which covers CJK, Latin, and
Vietnamese Extended Latin.
"""
from __future__ import annotations

from pathlib import Path
import shutil

_NOTO_FONT_PATH = Path(__file__).resolve().parent.parent.parent / "vendor" / "fonts" / "NotoSansCJKjp-Regular.otf"

# Vietnamese Extended Latin characters that most CJK-only fonts lack
_VIETNAMESE_CHARS = frozenset("đĐưƯơƠêÊôÔăĂâÂàÀáÁảẢãÃạẠèÈéÉẻẺẽẼẹẸìÌíÍỉỈĩĨịỊòÒóÓỏỎõÕọỌùÙúÚủỦũŨụỤýÝỷỶỹỸỵỴ")


def _has_vietnamese(text: str) -> bool:
    return any(c in _VIETNAMESE_CHARS for c in text)


def _existing_font_file(game_dir: Path) -> Path | None:
    """Return the path to the first .otf/.ttf/.ttc in www/fonts/ or data/fonts/."""
    for sub in ("www/fonts", "fonts", "data/fonts"):
        d = game_dir / sub
        if d.exists():
            for ext in (".otf", ".ttf", ".ttc", ".woff", ".woff2"):
                for f in d.glob(f"*{ext}"):
                    if f.is_file():
                        return f
    return None


def _read_text(path: Path, encoding: str = "utf-8-sig") -> str:
    try:
        return path.read_text(encoding=encoding)
    except (OSError, UnicodeDecodeError):
        return ""


def _write_text(path: Path, text: str, encoding: str = "utf-8-sig") -> None:
    path.write_text(text, encoding=encoding)


def _patch_gamefont_css(css_path: Path, noto_name: str) -> bool:
    """Patch www/fonts/gamefont.css to point to the Noto font."""
    text = _read_text(css_path)
    if not text:
        return False
    new_css = (
        "@font-face {\n"
        f'    font-family: GameFont;\n'
        f'    src: url("{noto_name}");\n'
        "}\n"
    )
    _write_text(css_path, new_css)
    return True


def _patch_forcefont_plugin(plugin_path: Path) -> bool:
    """Patch ForceFont.js to return a fallback font list that includes Noto."""
    text = _read_text(plugin_path)
    if not text or "standardFontFace" not in text:
        return False
    # Replace the return value with a font stack that prefers GameFont but falls back to Noto
    patched = text.replace(
        "return 'GameFont';",
        "return 'GameFont, Noto Sans CJK JP, Microsoft YaHei, Meiryo, sans-serif';"
    )
    if patched == text:
        # Maybe single-quoted or spaced differently
        patched = text.replace(
            'return "GameFont";',
            'return "GameFont, Noto Sans CJK JP, Microsoft YaHei, Meiryo, sans-serif";'
        )
    if patched != text:
        _write_text(plugin_path, patched)
        return True
    return False


def _patch_plugins_js_font(plugins_js: Path) -> bool:
    """Patch plugins.js ForceFont parameter if present."""
    text = _read_text(plugins_js)
    if not text or "standardFontFace" not in text:
        return False
    # If plugins.js contains the ForceFont code inline (rare), patch it
    patched = text.replace(
        "return 'GameFont';",
        "return 'GameFont, Noto Sans CJK JP, Microsoft YaHei, Meiryo, sans-serif';"
    )
    if patched != text:
        _write_text(plugins_js, patched)
        return True
    return False


def ensure_game_fonts_support(game_dir: Path, target_lang: str = "Vietnamese") -> dict[str, object]:
    """Check and replace game font with Vietnamese-capable font if needed.

    Returns a dict with 'replaced' (bool) and 'details' (str).
    """
    if target_lang.lower() not in {"vietnamese", "vi", "việt", "vie"}:
        return {"replaced": False, "details": "Target language is not Vietnamese; skipping font check"}

    if not _NOTO_FONT_PATH.exists():
        return {"replaced": False, "details": f"Bundled Noto font not found: {_NOTO_FONT_PATH}"}

    fonts_dir = game_dir / "www" / "fonts"
    if not fonts_dir.exists():
        fonts_dir = game_dir / "fonts"
        if not fonts_dir.exists():
            return {"replaced": False, "details": "No fonts directory found in game"}

    existing = _existing_font_file(game_dir)
    if existing is None:
        return {"replaced": False, "details": "No existing font file found"}

    # Determine if replacement is needed:
    # 1. If existing font is SourceHanSans* (no Vietnamese support)
    # 2. Or if it's MS Gothic / msgothic / Meiryo without Vietnamese
    need_replace = False
    existing_lower = existing.name.lower()
    known_no_vietnamese = (
        "sourcehansans", "sourcehan", "msmincho", "msgothic", "meiryo",
        "yugothic", "yumincho", "mplus", "ipagothic", "ipamincho",
        "notosanscjksc",  "notosanscjkkr",  "notosanscjktc",
    )
    if any(k in existing_lower for k in known_no_vietnamese):
        need_replace = True

    if not need_replace:
        return {"replaced": False, "details": f"Existing font {existing.name} may already support Vietnamese"}

    # Copy Noto font into the game's fonts directory
    noto_target = fonts_dir / "NotoSansCJKjp-Regular.otf"
    if not noto_target.exists() or noto_target.stat().st_size < _NOTO_FONT_PATH.stat().st_size:
        shutil.copy2(_NOTO_FONT_PATH, noto_target)

    details: list[str] = []

    # Patch gamefont.css
    css_path = fonts_dir / "gamefont.css"
    if css_path.exists() and _patch_gamefont_css(css_path, noto_target.name):
        details.append(f"Patched {css_path.name} -> {noto_target.name}")

    # Patch ForceFont.js plugin if present
    force_font = game_dir / "www" / "js" / "plugins" / "ForceFont.js"
    if force_font.exists() and _patch_forcefont_plugin(force_font):
        details.append(f"Patched {force_font.name}")

    # Also patch plugins.js if it has inline ForceFont code
    plugins_js = game_dir / "www" / "js" / "plugins.js"
    if plugins_js.exists() and _patch_plugins_js_font(plugins_js):
        details.append(f"Patched {plugins_js.name}")

    return {
        "replaced": True,
        "details": "; ".join(details) if details else f"Copied {noto_target.name} to fonts/",
        "old_font": existing.name,
        "new_font": noto_target.name,
    }
