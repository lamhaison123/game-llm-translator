from __future__ import annotations

from .game_tab import GameTabMixin
from .provider_tab import ProviderTabMixin
from .preview_tab import PreviewTabMixin
from .translate_tab import TranslateTabMixin
from .review_tab import ReviewTabMixin
from .apply_tab import ApplyTabMixin
from .backups_tab import BackupsTabMixin
from .log_tabs import LogTabsMixin

__all__ = [
    "GameTabMixin",
    "ProviderTabMixin",
    "PreviewTabMixin",
    "TranslateTabMixin",
    "ReviewTabMixin",
    "ApplyTabMixin",
    "BackupsTabMixin",
    "LogTabsMixin",
]