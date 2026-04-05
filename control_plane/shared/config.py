"""Configuration helpers — read from environment variables."""

from __future__ import annotations

import os


def normalize_model_name(name: str) -> str:
    """Return the model name unchanged; callers should use the full org/model form."""
    return name


def get_env(name: str, default: str | None = None) -> str:
    """Get an environment variable, raising if missing and no default."""
    value = os.environ.get(name, default)
    if value is None:
        raise RuntimeError(f"Missing required environment variable: {name}")
    return value


# Table / resource names (set by SAM template)
INSTANCES_TABLE = lambda: get_env("INSTANCES_TABLE")
MODELS_TABLE = lambda: get_env("MODELS_TABLE")
API_KEYS_TABLE = lambda: get_env("API_KEYS_TABLE")
ORCHESTRATOR_FUNCTION_NAME = lambda: get_env("ORCHESTRATOR_FUNCTION_NAME")
ALLOWED_EMAILS = lambda: get_env("ALLOWED_EMAILS", "")
GOOGLE_CLIENT_ID = lambda: get_env("GOOGLE_CLIENT_ID", "")
VLLM_PORT = 8000
