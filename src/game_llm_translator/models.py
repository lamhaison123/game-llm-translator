from dataclasses import dataclass
from pathlib import Path


@dataclass(slots=True)
class TextEntry:
    file: Path
    key: str
    source: str
    context: str = ""
    context_text: str = ""


@dataclass(slots=True)
class TranslationResult:
    file: Path
    key: str
    source: str
    target: str
    context: str = ""


def text_identity(file: Path | str, key: str) -> tuple[str, str]:
    return (Path(file).as_posix(), key)


def text_identity_id(file: Path | str, key: str) -> str:
    return f"{Path(file).as_posix()}\x1f{key}"