from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Settings:
    database_url: str
    model: str
    api_keys: tuple[str, ...]
    allowed_origins: tuple[str, ...]
    failure_simulation_enabled: bool
    request_timeout_seconds: float
    default_system_prompt: str


def get_settings() -> Settings:
    keys = tuple(
        key.strip()
        for key in os.getenv("LLM_API_KEYS", "").split(",")
        if key.strip() and not _is_placeholder_key(key.strip())
    )
    origins = tuple(
        origin.strip().rstrip("/")
        for origin in os.getenv(
            "ALLOWED_ORIGINS",
            "http://localhost:3000,http://127.0.0.1:3000,http://localhost:5173",
        ).split(",")
        if origin.strip()
    )
    return Settings(
        database_url=os.getenv("DATABASE_URL", "sqlite:///./data/app.db"),
        model=os.getenv("LLM_MODEL", "gemini/gemini-3.5-flash-lite"),
        api_keys=keys,
        allowed_origins=origins,
        failure_simulation_enabled=os.getenv(
            "ENABLE_FAILURE_SIMULATION", "false"
        ).lower()
        in {"1", "true", "yes", "on"},
        request_timeout_seconds=float(os.getenv("LLM_TIMEOUT_SECONDS", "15")),
        default_system_prompt=os.getenv(
            "DEFAULT_SYSTEM_PROMPT",
            "You are a helpful, concise AI assistant for an Indian small business. "
            "Always reply in clear, natural English, even if the customer writes in "
            "another language. Switch languages only when the customer explicitly "
            "asks you to. Ask one clear "
            "follow-up question when a request needs more information, and never "
            "claim that a booking, payment, or human handoff has happened unless "
            "the connected system confirms it.",
        ),
    )


def _is_placeholder_key(value: str) -> bool:
    """Keep sample values from making readiness look healthy by mistake."""
    normalized = value.casefold()
    return normalized.startswith(("paste_", "your-provider-key", "replace-me"))


def sqlite_path(database_url: str) -> str:
    prefix = "sqlite:///"
    if not database_url.startswith(prefix):
        raise ValueError("DATABASE_URL must use SQLite (sqlite:///...)")

    raw_path = database_url[len(prefix) :]
    if raw_path in {":memory:", "file::memory:?cache=shared"}:
        return raw_path

    path = Path(raw_path).expanduser()
    if not path.is_absolute():
        path = Path.cwd() / path
    path.parent.mkdir(parents=True, exist_ok=True)
    return str(path)

