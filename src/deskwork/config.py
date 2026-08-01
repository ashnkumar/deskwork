"""Configuration, read once from the environment.

Every value has a default that works inside `docker compose`. The only setting with no
sensible default is the API key.
"""

from __future__ import annotations

import os
from dataclasses import dataclass


def _int(name: str, default: int) -> int:
    raw = os.environ.get(name)
    if not raw:
        return default
    try:
        return int(raw)
    except ValueError as exc:
        raise ValueError(f"{name} must be an integer, got {raw!r}") from exc


@dataclass(frozen=True)
class Config:
    api_key: str
    model: str
    effort: str
    max_steps: int
    max_images: int
    display_width: int
    display_height: int
    portal_url: str
    database_url: str
    embed_model: str
    top_k: int

    @classmethod
    def from_env(cls) -> Config:
        return cls(
            api_key=os.environ.get("ANTHROPIC_API_KEY", ""),
            model=os.environ.get("DESKWORK_MODEL", "claude-opus-5"),
            effort=os.environ.get("DESKWORK_EFFORT", "high"),
            max_steps=_int("DESKWORK_MAX_STEPS", 40),
            max_images=_int("DESKWORK_MAX_IMAGES", 6),
            display_width=_int("DESKWORK_DISPLAY_WIDTH", 1024),
            display_height=_int("DESKWORK_DISPLAY_HEIGHT", 768),
            portal_url=os.environ.get("DESKWORK_PORTAL_URL", "http://portal:8000"),
            database_url=os.environ.get(
                "DATABASE_URL", "postgresql://deskwork:deskwork@db:5432/deskwork"
            ),
            embed_model=os.environ.get("DESKWORK_EMBED_MODEL", "BAAI/bge-small-en-v1.5"),
            top_k=_int("DESKWORK_TOP_K", 4),
        )

    def require_api_key(self) -> str:
        """Fail loudly and early rather than on the first HTTP 401."""
        if not self.api_key:
            raise RuntimeError(
                "ANTHROPIC_API_KEY is not set. Copy .env.example to .env and add your key "
                "from https://console.anthropic.com/settings/keys"
            )
        return self.api_key
