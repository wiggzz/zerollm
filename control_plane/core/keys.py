"""API key CRUD core logic."""

from __future__ import annotations

import secrets
import time

from control_plane.core.auth import hash_api_key
from control_plane.core.interfaces import StateStore


KEY_PREFIX = "zllm"


def _new_token() -> str:
    return f"{KEY_PREFIX}-{secrets.token_urlsafe(32)}"


def create_key(email: str, name: str, state: StateStore) -> dict:
    """Create and store a new API key for an email address."""
    raw_key = _new_token()
    key_hash = hash_api_key(raw_key)
    record = {
        "key_hash": key_hash,
        "email": email,
        "name": name,
        "created_at": int(time.time()),
    }
    state.put_api_key(record)
    return {
        "key": raw_key,
        "key_id": key_hash,
        "name": name,
        "created_at": record["created_at"],
    }


def list_keys(email: str, state: StateStore) -> list[dict]:
    """List key metadata for an email address (without raw secrets)."""
    keys = state.list_api_keys(email)
    return [
        {
            "key_id": k["key_hash"],
            "name": k.get("name", "default"),
            "created_at": k.get("created_at"),
        }
        for k in sorted(keys, key=lambda item: item.get("created_at", 0), reverse=True)
    ]


def delete_key(key_hash: str, email: str, state: StateStore) -> None:
    """Delete a key if it exists and belongs to the email address."""
    record = state.get_api_key(key_hash)
    if not record:
        return
    if record.get("email") != email:
        return
    state.delete_api_key(key_hash)
