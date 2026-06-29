"""Application configuration.

All secrets and tunables come from environment variables so the same code runs
against OpenAI or Azure OpenAI without modification. A local ``.env`` file is
loaded if present (see ``.env.example``).
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path


def _load_dotenv() -> None:
    """Minimal .env loader (no external dependency).

    Lines like ``KEY=value`` are loaded into ``os.environ`` if not already set.
    Quotes around the value are stripped. Comment lines (``#``) are ignored.
    """
    env_path = BASE_DIR / ".env"
    if not env_path.exists():
        return
    for raw in env_path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value


BASE_DIR = Path(__file__).resolve().parent.parent
ADMIN_DIR = BASE_DIR / "admin"
STATIC_DIR = BASE_DIR / "static"
DATA_DIR = BASE_DIR / "data"

EVAL_RULE_PATH = ADMIN_DIR / "eval-rule.md"
REFERENCE_ANSWERS_PATH = ADMIN_DIR / "reference_answers.json"
DB_PATH = DATA_DIR / "scores.db"


@dataclass
class Settings:
    """Resolved runtime settings."""

    # --- LLM provider ---
    # If azure_endpoint is set, the Azure OpenAI client is used; otherwise the
    # standard OpenAI client (optionally pointed at base_url) is used.
    openai_api_key: str = ""
    openai_base_url: str = ""  # optional; for OpenAI-compatible gateways
    model: str = "gpt-4o-mini"  # OpenAI model name OR Azure deployment name

    azure_endpoint: str = ""
    azure_api_key: str = ""
    azure_api_version: str = "2024-10-21"

    # --- Scoring ---
    judge_count: int = 3            # number of independent LLM judges per item
    max_submissions: int = 4        # per employee_id
    max_concurrency: int = 6        # simultaneous in-flight LLM calls
    judge_max_retries: int = 3      # retries per judge on transient error
    request_timeout: float = 60.0   # seconds per LLM call

    # --- Server ---
    leaderboard_poll_seconds: int = 5

    def uses_azure(self) -> bool:
        return bool(self.azure_endpoint)


def _int_env(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, default))
    except (TypeError, ValueError):
        return default


def _float_env(name: str, default: float) -> float:
    try:
        return float(os.environ.get(name, default))
    except (TypeError, ValueError):
        return default


def load_settings() -> Settings:
    _load_dotenv()
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    return Settings(
        openai_api_key=os.environ.get("OPENAI_API_KEY", ""),
        openai_base_url=os.environ.get("OPENAI_BASE_URL", ""),
        model=os.environ.get("LLM_MODEL", "gpt-4o-mini"),
        azure_endpoint=os.environ.get("AZURE_OPENAI_ENDPOINT", ""),
        azure_api_key=os.environ.get("AZURE_OPENAI_API_KEY", ""),
        azure_api_version=os.environ.get("AZURE_OPENAI_API_VERSION", "2024-10-21"),
        judge_count=_int_env("JUDGE_COUNT", 3),
        max_submissions=_int_env("MAX_SUBMISSIONS", 4),
        max_concurrency=_int_env("MAX_CONCURRENCY", 6),
        judge_max_retries=_int_env("JUDGE_MAX_RETRIES", 3),
        request_timeout=_float_env("LLM_REQUEST_TIMEOUT", 60.0),
        leaderboard_poll_seconds=_int_env("LEADERBOARD_POLL_SECONDS", 5),
    )


settings = load_settings()
