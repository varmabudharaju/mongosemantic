from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Literal

from mongosemantic import connection_store

KNOWN_MODELS: tuple[str, ...] = (
    "local-fast",
    "local-better",
    "openai-small",
    "openai-large",
    "ollama-nomic",
)

MODEL_DIMS: dict[str, int] = {
    "local-fast": 384,
    "local-better": 768,
    "openai-small": 1536,
    "openai-large": 3072,
    "ollama-nomic": 768,
}

Source = Literal["env", "file", "none"]


@dataclass
class Settings:
    uri: str = field(default_factory=lambda: os.environ.get("MONGOSEMANTIC_URI", ""))
    database: str = field(default_factory=lambda: os.environ.get("MONGOSEMANTIC_DB", ""))
    model: str = field(default_factory=lambda: os.environ.get("MONGOSEMANTIC_MODEL", "local-fast"))
    batch_size: int = field(
        default_factory=lambda: int(os.environ.get("MONGOSEMANTIC_BATCH_SIZE", "32"))
    )
    poll_interval_seconds: int = field(
        default_factory=lambda: int(os.environ.get("MONGOSEMANTIC_POLL_INTERVAL_SECONDS", "30"))
    )
    openai_api_key: str = field(default_factory=lambda: os.environ.get("OPENAI_API_KEY", ""))
    ollama_host: str = field(
        default_factory=lambda: os.environ.get("OLLAMA_HOST", "http://localhost:11434")
    )
    source: Source = "env"

    def __post_init__(self) -> None:
        if not self.uri:
            raise ValueError("MONGOSEMANTIC_URI is required")
        if not (self.uri.startswith("mongodb://") or self.uri.startswith("mongodb+srv://")):
            raise ValueError("MONGOSEMANTIC_URI must start with mongodb:// or mongodb+srv://")
        if self.model not in KNOWN_MODELS:
            raise ValueError(
                f"Unknown model '{self.model}'. Expected one of: {', '.join(KNOWN_MODELS)}"
            )
        if not self.database:
            raise ValueError("MONGOSEMANTIC_DB is required")

    @classmethod
    def from_environment(cls) -> Settings:
        """Layer env vars over the saved config file.

        Precedence (highest first):
          1. MONGOSEMANTIC_URI / MONGOSEMANTIC_DB env vars (source="env")
          2. ~/.config/mongosemantic/config.json                (source="file")
          3. raise ValueError                                   (no source available)
        """
        env_uri = os.environ.get("MONGOSEMANTIC_URI", "")
        env_db = os.environ.get("MONGOSEMANTIC_DB", "")
        if env_uri and env_db:
            return cls(uri=env_uri, database=env_db, source="env")

        saved = connection_store.load()
        if saved is not None:
            return cls(uri=saved.uri, database=saved.database, source="file")

        # Fall through: rely on Settings() to raise the canonical error.
        return cls()  # source defaults to "env"; will raise in __post_init__

    @classmethod
    def try_from_environment(cls) -> Settings | None:
        """Like from_environment but returns None instead of raising.

        Used by routes that need to detect "not connected" cleanly.
        """
        try:
            return cls.from_environment()
        except ValueError:
            return None
