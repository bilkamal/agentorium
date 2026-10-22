"""Server settings and environment seeding.

Agents themselves live in `data/agents.json` (see registry.py) and are edited from the GUI.
Environment variables only provide server settings and the *initial* seed of agents:

  OFFICE_DEFAULT_PROVIDER / _MODEL / _API_KEY / _BASE_URL / _MAX_TOKENS   defaults for every agent
  OFFICE_<A|B|C>_PROVIDER / _MODEL / _API_KEY / _BASE_URL / _EFFORT ...  seed for the three starter agents
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")

PROVIDERS = ("anthropic", "openai", "mock")


def env_agent(agent: str, field: str, default: str | None = None) -> str | None:
    """Return OFFICE_<agent>_<field>, falling back to OFFICE_DEFAULT_<field>."""
    for key in (f"OFFICE_{agent}_{field}", f"OFFICE_DEFAULT_{field}"):
        value = os.getenv(key)
        if value is not None and value.strip():
            return value.strip()
    return default


@dataclass(frozen=True)
class Settings:
    host: str
    port: int
    max_revisions: int
    doc_max_chars: int
    anthropic_fallbacks: bool
    data_dir: Path
    lang: str          # "id" | "en": default UI language, mock replies, system notes
    api_token: str     # optional shared secret for the external events API ("" = open)

    @classmethod
    def from_env(cls) -> "Settings":
        return cls(
            host=os.getenv("OFFICE_HOST", "127.0.0.1"),
            port=int(os.getenv("OFFICE_PORT", "8000")),
            max_revisions=int(os.getenv("OFFICE_MAX_REVISIONS", "2")),
            doc_max_chars=int(os.getenv("OFFICE_DOC_MAX_CHARS", "60000")),
            anthropic_fallbacks=os.getenv("OFFICE_ANTHROPIC_FALLBACKS", "1") not in ("0", "false", "no"),
            data_dir=Path(os.getenv("OFFICE_DATA_DIR", str(ROOT / "data"))),
            lang=(os.getenv("OFFICE_LANG", "id").strip().lower() or "id"),
            api_token=os.getenv("OFFICE_API_TOKEN", "").strip(),
        )


settings = Settings.from_env()
