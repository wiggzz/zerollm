"""Unit tests for cluster core logic (Phase 4)."""

from __future__ import annotations

import pytest

from control_plane.core.cluster import get_cluster_state, manual_scale


def test_get_cluster_state_reports_cold_and_ready_models(state):
    state.put_model_config(
        {
            "name": "Meta/Llama-3",
            "instance_type": "g5.xlarge",
            "idle_timeout": 300,
        }
    )
    state.put_instance(
        {
            "instance_id": "model#Qwen/Qwen3-32B",
            "provider_instance_id": "i-ready",
            "model": "Qwen/Qwen3-32B",
            "status": "ready",
            "ip": "127.0.0.1",
            "instance_type": "g5.xlarge",
            "launched_at": 1,
            "last_request_at": 1,
        }
    )

    result = get_cluster_state(state)

    by_name = {model["name"]: model for model in result["models"]}
    assert by_name["Qwen/Qwen3-32B"]["status"] == "ready"
    assert by_name["Qwen/Qwen3-32B"]["ready_count"] == 1
    assert by_name["Meta/Llama-3"]["status"] == "cold"


def test_manual_scale_up_triggers_orchestrator(state, compute):
    triggered = []

    result = manual_scale(
        model="Qwen/Qwen3-32B",
        action="up",
        state=state,
        compute=compute,
        trigger_scale_up=triggered.append,
    )

    assert result["ok"] is True
    assert triggered == ["Qwen/Qwen3-32B"]


def test_manual_scale_down_terminates_one_instance(state, compute):
    state.put_instance(
        {
            "instance_id": "model#Qwen/Qwen3-32B",
            "provider_instance_id": "i-ready",
            "model": "Qwen/Qwen3-32B",
            "status": "ready",
            "ip": "127.0.0.1",
            "instance_type": "g5.xlarge",
            "launched_at": 1,
            "last_request_at": 1,
        }
    )

    result = manual_scale(
        model="Qwen/Qwen3-32B",
        action="down",
        state=state,
        compute=compute,
        trigger_scale_up=lambda *_: None,
    )

    assert result["terminated_instance_id"] == "model#Qwen/Qwen3-32B"
    assert state.get_instance("model#Qwen/Qwen3-32B")["status"] == "terminated"
    assert "i-ready" in compute.terminated


@pytest.mark.parametrize("model,action", [("", "up"), ("missing/model", "up"), ("Qwen/Qwen3-32B", "bad")])
def test_manual_scale_rejects_invalid_inputs(state, compute, model, action):
    with pytest.raises(ValueError):
        manual_scale(model=model, action=action, state=state, compute=compute, trigger_scale_up=lambda *_: None)
