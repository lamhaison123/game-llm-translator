from __future__ import annotations

from .llm import TOKEN_PATTERN

_PLACEHOLDER_RE = TOKEN_PATTERN


def translation_warnings(source: str, target: str) -> list[str]:
    """Return non-fatal quality warnings for a source/target pair."""
    if not target.strip() or target == source:
        return []
    warnings: list[str] = []
    for match in _PLACEHOLDER_RE.finditer(source):
        token = match.group(0)
        if token not in target:
            warnings.append(f"missing placeholder {token!r}")
    src_newlines = source.count("\n")
    tgt_newlines = target.count("\n")
    if src_newlines and tgt_newlines != src_newlines:
        warnings.append(f"newline count mismatch ({src_newlines} vs {tgt_newlines})")
    _cjk_short = len(source) <= 10 and any('\u4e00' <= c <= '\u9fff' or '\u3040' <= c <= '\u30ff' for c in source)
    _ratio = 8 if _cjk_short else 3
    if len(target) > len(source) * _ratio and len(source) < 120:
        warnings.append("translation much longer than source (possible UI overflow)")
    return warnings
