"""LLM client factory — provider-agnostic (OpenAI / Azure OpenAI).

Both branches return an object exposing the same
``chat.completions.create(...)`` async interface, so callers in ``scoring.py``
never depend on which provider is configured.
"""

from __future__ import annotations

from functools import lru_cache

from openai import AsyncAzureOpenAI, AsyncOpenAI

from .config import Settings, settings


def _build_client(cfg: Settings):
    if cfg.uses_azure():
        if not cfg.azure_api_key:
            raise RuntimeError(
                "AZURE_OPENAI_ENDPOINT is set but AZURE_OPENAI_API_KEY is missing."
            )
        return AsyncAzureOpenAI(
            azure_endpoint=cfg.azure_endpoint,
            api_key=cfg.azure_api_key,
            api_version=cfg.azure_api_version,
            timeout=cfg.request_timeout,
        )

    if not cfg.openai_api_key:
        raise RuntimeError(
            "OPENAI_API_KEY is missing. Set it (or AZURE_OPENAI_ENDPOINT + "
            "AZURE_OPENAI_API_KEY for Azure) in your environment / .env file."
        )
    kwargs = {"api_key": cfg.openai_api_key, "timeout": cfg.request_timeout}
    if cfg.openai_base_url:
        kwargs["base_url"] = cfg.openai_base_url
    return AsyncOpenAI(**kwargs)


@lru_cache(maxsize=1)
def get_client():
    """Return a cached async client for the configured provider."""
    return _build_client(settings)


def get_model_name() -> str:
    """Model name (OpenAI) or deployment name (Azure) used for completions."""
    return settings.model
