"""Unit tests for API key auth core logic (Phase 3)."""

from __future__ import annotations

from control_plane.core.auth import hash_api_key, validate_api_key


def test_validate_api_key_returns_true_for_stored_key(state):
    token = "zllm-test-token"
    state.put_api_key(
        {
            "key_hash": hash_api_key(token),
            "email": "test@example.com",
            "name": "laptop",
            "created_at": 1,
        }
    )

    valid, email = validate_api_key(token, state)

    assert valid is True
    assert email == "test@example.com"


def test_validate_api_key_returns_false_for_unknown_key(state):
    valid, email = validate_api_key("zllm-missing", state)

    assert valid is False
    assert email == ""
