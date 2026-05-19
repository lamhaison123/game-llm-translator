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
