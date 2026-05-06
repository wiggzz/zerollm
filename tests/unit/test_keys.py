"""Unit tests for API key CRUD core logic (Phase 3)."""

from __future__ import annotations

from control_plane.core.auth import validate_api_key
from control_plane.core.keys import create_key, delete_key, list_keys


def test_create_key_persists_hashed_record(state):
    created = create_key("owner@example.com", "laptop", state)

    assert created["key"].startswith("zllm-")
    assert created["key_id"]

    valid, email = validate_api_key(created["key"], state)
    assert valid is True
    assert email == "owner@example.com"


def test_list_keys_returns_only_owner_keys(state):
    k1 = create_key("a@example.com", "one", state)
    create_key("a@example.com", "two", state)
    create_key("b@example.com", "other", state)

    keys = list_keys("a@example.com", state)

    assert len(keys) == 2
    ids = {k["key_id"] for k in keys}
    assert k1["key_id"] in ids


def test_delete_key_only_deletes_owned_key(state):
    mine = create_key("me@example.com", "mine", state)
    other = create_key("other@example.com", "other", state)

    delete_key(mine["key_id"], "me@example.com", state)
    delete_key(other["key_id"], "me@example.com", state)

    mine_valid, _ = validate_api_key(mine["key"], state)
    other_valid, _ = validate_api_key(other["key"], state)
    assert mine_valid is False
    assert other_valid is True
