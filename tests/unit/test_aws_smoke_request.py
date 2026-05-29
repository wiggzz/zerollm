"""Unit tests for the deployed AWS smoke request helper."""

from __future__ import annotations

import importlib.util
from pathlib import Path


def _load_aws_smoke_request():
    script_path = Path(__file__).resolve().parents[2] / "scripts" / "aws_smoke_request.py"
    spec = importlib.util.spec_from_file_location("aws_smoke_request", script_path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_chat_completion_payload_uses_larger_completion_budget():
    aws_smoke_request = _load_aws_smoke_request()

    payload = aws_smoke_request.chat_completion_payload(
        "Qwen/Qwen3.5-4B",
        "Reply with exactly: zerollm smoke ok",
        1024,
    )

    assert payload == {
        "model": "Qwen/Qwen3.5-4B",
        "messages": [{"role": "user", "content": "Reply with exactly: zerollm smoke ok"}],
        "max_tokens": 1024,
        "temperature": 0,
        "reasoning_effort": "none",
    }
