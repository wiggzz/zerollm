"""Unit tests for core orchestrator logic (Phase 1)."""

from __future__ import annotations

from control_plane.core import orchestrator


def test_scale_up_launches_and_returns_starting(state, compute):
    result = orchestrator.scale_up("Qwen/Qwen3-32B", state, compute)

    assert result["status"] == "starting"
    assert len(compute.launched) == 1
    assert result["instance_id"] == "model#Qwen/Qwen3-32B"
    assert result["provider_instance_id"].startswith("i-mock-")

    saved = state.get_instance(result["instance_id"])
    assert saved is not None
    assert saved["status"] == "starting"


def test_scale_up_is_idempotent_when_ready_instance_exists(state, compute):
    state.put_instance(
        {
            "instance_id": "model#Qwen/Qwen3-32B",
            "provider_instance_id": "i-existing",
            "model": "Qwen/Qwen3-32B",
            "status": "ready",
            "ip": "10.0.0.1",
            "instance_type": "g5.xlarge",
            "launched_at": 1,
            "last_request_at": 1,
        }
    )

    result = orchestrator.scale_up("Qwen/Qwen3-32B", state, compute)

    assert result["provider_instance_id"] == "i-existing"
    assert len(compute.launched) == 0


def test_scale_up_unknown_model_raises(state, compute):
    try:
        orchestrator.scale_up("Unknown/Model", state, compute)
        assert False, "Expected ValueError for unknown model"
    except ValueError as exc:
        assert "Unknown model" in str(exc)


def test_scale_up_deduplicates_when_claim_already_exists(state, compute):
    state.put_instance(
        {
            "instance_id": "model#Qwen/Qwen3-32B",
            "model": "Qwen/Qwen3-32B",
            "status": "starting",
            "ip": "",
            "instance_type": "g5.xlarge",
            "launched_at": 1,
            "last_request_at": 1,
        }
    )

    result = orchestrator.scale_up("Qwen/Qwen3-32B", state, compute)

    assert result["status"] == "starting"
    assert len(compute.launched) == 0


def test_scale_up_handles_claim_race_without_launch(state, compute):
    original = state.put_instance_if_absent

    def losing_claim(instance: dict) -> bool:
        state.put_instance(instance)
        return False

    state.put_instance_if_absent = losing_claim

    result = orchestrator.scale_up("Qwen/Qwen3-32B", state, compute)

    assert result["status"] == "starting"
    assert result["instance_id"] == "model#Qwen/Qwen3-32B"
    assert len(compute.launched) == 0
    state.put_instance_if_absent = original


def test_check_health_marks_ready_when_healthy(monkeypatch, state, compute):
    now = 10_000
    monkeypatch.setattr(orchestrator.time, "time", lambda: now)

    state.put_instance(
        {
            "instance_id": "model#Qwen/Qwen3-32B",
            "provider_instance_id": "i-abc",
            "model": "Qwen/Qwen3-32B",
            "status": "starting",
            "ip": "1.2.3.4",
            "instance_type": "g5.xlarge",
            "launched_at": now - 60,
            "last_request_at": now - 60,
        }
    )

    import requests

    class MockResp:
        status_code = 200

    monkeypatch.setattr(requests, "get", lambda *a, **kw: MockResp())

    result = orchestrator.check_health(state, compute)

    assert "model#Qwen/Qwen3-32B" in result["became_ready"]
    assert state.get_instance("model#Qwen/Qwen3-32B")["status"] == "ready"


def test_check_health_still_starting_when_unhealthy(monkeypatch, state, compute):
    now = 10_000
    monkeypatch.setattr(orchestrator.time, "time", lambda: now)

    state.put_instance(
        {
            "instance_id": "model#Qwen/Qwen3-32B",
            "provider_instance_id": "i-abc",
            "model": "Qwen/Qwen3-32B",
            "status": "starting",
            "ip": "1.2.3.4",
            "instance_type": "g5.xlarge",
            "launched_at": now - 60,
            "last_request_at": now - 60,
        }
    )

    import requests

    monkeypatch.setattr(requests, "get", lambda *a, **kw: (_ for _ in ()).throw(requests.ConnectionError()))

    result = orchestrator.check_health(state, compute)

    assert "model#Qwen/Qwen3-32B" in result["still_starting"]
    assert state.get_instance("model#Qwen/Qwen3-32B")["status"] == "starting"


def test_check_health_terminates_on_timeout(monkeypatch, state, compute):
    now = 10_000
    monkeypatch.setattr(orchestrator.time, "time", lambda: now)

    state.put_instance(
        {
            "instance_id": "model#Qwen/Qwen3-32B",
            "provider_instance_id": "i-abc",
            "model": "Qwen/Qwen3-32B",
            "status": "starting",
            "ip": "1.2.3.4",
            "instance_type": "g5.xlarge",
            "launched_at": now - orchestrator.MAX_START_SECONDS - 1,
            "last_request_at": now - orchestrator.MAX_START_SECONDS - 1,
        }
    )

    result = orchestrator.check_health(state, compute)

    assert "model#Qwen/Qwen3-32B" in result["terminated"]
    assert "i-abc" in compute.terminated
    assert state.get_instance("model#Qwen/Qwen3-32B")["status"] == "terminated"


def test_scale_down_terminates_only_idle_instances(monkeypatch, state, compute):
    now = 10_000
    monkeypatch.setattr(orchestrator.time, "time", lambda: now)

    state.put_instance(
        {
            "instance_id": "model#Qwen/Qwen3-32B",
            "provider_instance_id": "i-idle",
            "model": "Qwen/Qwen3-32B",
            "status": "ready",
            "ip": "10.0.0.1",
            "instance_type": "g5.xlarge",
            "launched_at": now - 1000,
            "last_request_at": now - 500,
        }
    )
    state.put_instance(
        {
            "instance_id": "model#Meta/Llama-3",
            "provider_instance_id": "i-active",
            "model": "Meta/Llama-3",
            "status": "ready",
            "ip": "10.0.0.2",
            "instance_type": "g5.xlarge",
            "launched_at": now - 100,
            "last_request_at": now - 100,
        }
    )
    state.put_model_config(
        {
            "name": "Meta/Llama-3",
            "instance_type": "g5.xlarge",
            "idle_timeout": 300,
        }
    )

    terminated = orchestrator.scale_down(state, compute)

    assert terminated == ["model#Qwen/Qwen3-32B"]
    assert compute.terminated == ["i-idle"]
    assert state.get_instance("model#Qwen/Qwen3-32B")["status"] == "terminated"
    assert state.get_instance("model#Meta/Llama-3")["status"] == "ready"
