"""E2E lifecycle test using mock backends + mock vLLM server."""

from __future__ import annotations

from control_plane.backends.mock.compute import MockComputeBackend
from control_plane.backends.mock.state import InMemoryStateStore
from control_plane.core import orchestrator


def test_full_cold_start_inference_and_scale_down_cycle(mock_vllm, monkeypatch):
    state = InMemoryStateStore()
    state.put_model_config(
        {
            "name": "Qwen/Qwen3-32B",
            "instance_type": "g5.xlarge",
            "idle_timeout": 1,
        }
    )
    compute = MockComputeBackend(mock_ip=mock_vllm.host)

    monkeypatch.setattr(orchestrator, "SERVER_PORT", mock_vllm.port)
    up = orchestrator.scale_up("Qwen/Qwen3-32B", state, compute)
    assert up["status"] == "starting"
    health = orchestrator.check_health(state, compute)
    assert health["became_ready"] == ["model#Qwen/Qwen3-32B"]

    instance = state.get_instance("model#Qwen/Qwen3-32B")
    state.update_instance(instance["instance_id"], last_request_at=0)
    monkeypatch.setattr(orchestrator.time, "time", lambda: 10)

    result = orchestrator.scale_down(state, compute)
    assert result == {"stopping": ["model#Qwen/Qwen3-32B"], "stopped": [], "terminated": []}
    assert state.get_instance("model#Qwen/Qwen3-32B")["status"] == "stopping"
