from __future__ import annotations

import os
from dataclasses import dataclass
from dotenv import load_dotenv


@dataclass(slots=True)
class Settings:
    provider: str
    model: str
    batch_size: int = 30


def load_settings(provider: str | None = None, model: str | None = None, batch_size: int = 30) -> Settings:
    load_dotenv()
    # Default anthropic for `translate`; CLI `auto` passes --provider google explicitly.
    selected_provider = (provider or os.getenv("LLM_PROVIDER") or "anthropic").lower()
    model_defaults = {
        "anthropic": "claude-opus-4-7",
        "openai": "gpt-4.1-mini",
        "openai-compatible": os.getenv("OPENAI_COMPATIBLE_MODEL") or "gpt-4.1-mini",
    }
    default_model = model_defaults.get(selected_provider, selected_provider)
    return Settings(
        provider=selected_provider,
        model=model or os.getenv("LLM_MODEL") or default_model,
        batch_size=batch_size,
    )
