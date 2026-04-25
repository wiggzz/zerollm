"""Unit tests for model seeding helpers."""

from __future__ import annotations

import importlib.util
from pathlib import Path


def _load_seed_models():
    script_path = Path(__file__).resolve().parents[2] / "scripts" / "seed_models.py"
    spec = importlib.util.spec_from_file_location("seed_models", script_path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_configure_hf_transfer_environment_sets_fast_xet_defaults(monkeypatch):
    seed_models = _load_seed_models()
    monkeypatch.delenv("HF_XET_HIGH_PERFORMANCE", raising=False)
    monkeypatch.delenv("HF_XET_NUM_CONCURRENT_RANGE_GETS", raising=False)

    seed_models.configure_hf_transfer_environment()

    assert seed_models.os.environ["HF_XET_HIGH_PERFORMANCE"] == "1"
    assert seed_models.os.environ["HF_XET_NUM_CONCURRENT_RANGE_GETS"] == "64"


def test_configure_hf_transfer_environment_preserves_overrides(monkeypatch):
    seed_models = _load_seed_models()
    monkeypatch.setenv("HF_XET_HIGH_PERFORMANCE", "0")
    monkeypatch.setenv("HF_XET_NUM_CONCURRENT_RANGE_GETS", "24")

    seed_models.configure_hf_transfer_environment()

    assert seed_models.os.environ["HF_XET_HIGH_PERFORMANCE"] == "0"
    assert seed_models.os.environ["HF_XET_NUM_CONCURRENT_RANGE_GETS"] == "24"


def test_load_models_reads_repo_manifest():
    seed_models = _load_seed_models()

    models = seed_models.load_models()

    assert [model["name"] for model in models] == [
        "Qwen/Qwen3.6-27B",
        "Qwen/Qwen3.5-4B",
    ]
    assert models[0]["hf_repo"] == "unsloth/Qwen3.6-27B-GGUF"
    assert models[0]["hf_file"] == "Qwen3.6-27B-Q4_K_M.gguf"


class _FakeTable:
    def __init__(self):
        self.deleted = []

    def scan(self, **kwargs):
        return {
            "Items": [
                {"name": "Qwen/Qwen3.6-27B"},
                {"name": "Qwen/Qwen3.5-27B"},
                {"name": "Qwen/Qwen3.5-4B"},
            ]
        }

    def delete_item(self, **kwargs):
        self.deleted.append(kwargs["Key"]["name"])


def test_prune_stale_models_deletes_rows_absent_from_manifest():
    seed_models = _load_seed_models()
    table = _FakeTable()

    deleted = seed_models.prune_stale_models(
        table,
        {"Qwen/Qwen3.6-27B", "Qwen/Qwen3.5-4B"},
    )

    assert deleted == ["Qwen/Qwen3.5-27B"]
    assert table.deleted == ["Qwen/Qwen3.5-27B"]
