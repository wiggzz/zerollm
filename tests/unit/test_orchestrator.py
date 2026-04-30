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


def test_scale_up_is_idempotent_when_busy_instance_exists(state, compute):
    state.put_instance(
        {
            "instance_id": "model#Qwen/Qwen3-32B",
            "provider_instance_id": "i-existing",
            "model": "Qwen/Qwen3-32B",
            "status": "busy",
            "ip": "10.0.0.1",
            "instance_type": "g5.xlarge",
            "launched_at": 1,
            "last_request_at": 1,
            "active_request_started_at": 1,
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

    result = orchestrator.scale_down(state, compute)

    assert result == {"stopping": ["model#Qwen/Qwen3-32B"], "stopped": [], "terminated": []}
    assert compute.stopped == ["i-idle"]
    idle = state.get_instance("model#Qwen/Qwen3-32B")
    assert idle["status"] == "stopping"
    assert idle["warm_expires_at"] == now + orchestrator.DEFAULT_WARM_TIMEOUT_SECONDS
    assert state.get_instance("model#Meta/Llama-3")["status"] == "ready"


def test_scale_down_terminates_when_warm_timeout_disabled(monkeypatch, state, compute):
    now = 10_000
    monkeypatch.setattr(orchestrator.time, "time", lambda: now)
    state.put_model_config(
        {
            "name": "NoWarm/Model",
            "instance_type": "g5.xlarge",
            "idle_timeout": 300,
            "warm_timeout": 0,
        }
    )
    state.put_instance(
        {
            "instance_id": "model#NoWarm/Model",
            "provider_instance_id": "i-idle",
            "model": "NoWarm/Model",
            "status": "ready",
            "ip": "10.0.0.1",
            "instance_type": "g5.xlarge",
            "launched_at": now - 1000,
            "last_request_at": now - 500,
        }
    )

    result = orchestrator.scale_down(state, compute)

    assert result == {"stopping": [], "stopped": [], "terminated": ["model#NoWarm/Model"]}
    assert compute.terminated == ["i-idle"]
    assert state.get_instance("model#NoWarm/Model")["status"] == "terminated"


def test_scale_down_rolls_back_when_stop_fails(monkeypatch, state, compute):
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

    def fail_stop(_instance_id):
        raise RuntimeError("stop failed")

    compute.stop = fail_stop

    try:
        orchestrator.scale_down(state, compute)
        assert False, "Expected stop failure"
    except RuntimeError:
        pass

    inst = state.get_instance("model#Qwen/Qwen3-32B")
    assert inst["status"] == "ready"
    assert inst["last_request_at"] == now
    assert inst["stop_error_at"] == now


def test_scale_down_recovers_stale_stopping_instance(monkeypatch, state, compute):
    now = 10_000
    monkeypatch.setattr(orchestrator.time, "time", lambda: now)
    compute.instance_states["i-stopping"] = "running"
    state.put_instance(
        {
            "instance_id": "model#Qwen/Qwen3-32B",
            "provider_instance_id": "i-stopping",
            "model": "Qwen/Qwen3-32B",
            "status": "stopping",
            "ip": "10.0.0.1",
            "instance_type": "g5.xlarge",
            "launched_at": now - 1000,
            "last_request_at": now - 500,
            "stopping_at": now - orchestrator.STOPPING_RECOVERY_SECONDS - 1,
        }
    )

    orchestrator.scale_down(state, compute)

    inst = state.get_instance("model#Qwen/Qwen3-32B")
    assert inst["status"] == "ready"
    assert inst["last_request_at"] == now
    assert compute.stopped == []


def test_scale_down_finalizes_provider_stopped_instance(monkeypatch, state, compute):
    now = 10_000
    monkeypatch.setattr(orchestrator.time, "time", lambda: now)
    compute.instance_states["i-stopping"] = "stopped"
    state.put_instance(
        {
            "instance_id": "model#Qwen/Qwen3-32B",
            "provider_instance_id": "i-stopping",
            "model": "Qwen/Qwen3-32B",
            "status": "stopping",
            "ip": "10.0.0.1",
            "instance_type": "g5.xlarge",
            "launched_at": now - 1000,
            "last_request_at": now - 500,
            "stopping_at": now - 60,
            "warm_expires_at": now + 1000,
        }
    )

    orchestrator.scale_down(state, compute)

    inst = state.get_instance("model#Qwen/Qwen3-32B")
    assert inst["status"] == "stopped"
    assert inst["ip"] == ""
    assert inst["stopped_at"] == now


def test_scale_down_reconciles_ready_row_when_provider_is_stopped(monkeypatch, state, compute):
    now = 10_000
    monkeypatch.setattr(orchestrator.time, "time", lambda: now)
    compute.instance_states["i-stopped"] = "stopped"
    state.put_instance(
        {
            "instance_id": "model#Qwen/Qwen3-32B",
            "provider_instance_id": "i-stopped",
            "model": "Qwen/Qwen3-32B",
            "status": "ready",
            "ip": "10.0.0.1",
            "instance_type": "g5.xlarge",
            "launched_at": now - 1000,
            "last_request_at": now - 500,
        }
    )

    result = orchestrator.scale_down(state, compute)

    assert result == {"stopping": [], "stopped": ["model#Qwen/Qwen3-32B"], "terminated": []}
    inst = state.get_instance("model#Qwen/Qwen3-32B")
    assert inst["status"] == "stopped"
    assert inst["ip"] == ""


def test_scale_up_starts_warm_instance(monkeypatch, state, compute):
    now = 10_000
    monkeypatch.setattr(orchestrator.time, "time", lambda: now)
    state.put_instance(
        {
            "instance_id": "model#Qwen/Qwen3-32B",
            "provider_instance_id": "i-warm",
            "model": "Qwen/Qwen3-32B",
            "status": "stopped",
            "ip": "",
            "previous_ip": "10.0.0.1",
            "instance_type": "g5.xlarge",
            "launched_at": now - 1000,
            "last_request_at": now - 500,
            "stopped_at": now - 100,
            "warm_expires_at": now + 100,
        }
    )

    result = orchestrator.scale_up("Qwen/Qwen3-32B", state, compute)

    assert result["status"] == "starting"
    assert result["provider_instance_id"] == "i-warm"
    assert result["ip"] == "127.0.0.1"
    assert compute.started == ["i-warm"]
    assert len(compute.launched) == 0


def test_scale_up_terminates_expired_warm_instance_then_launches(monkeypatch, state, compute):
    now = 10_000
    monkeypatch.setattr(orchestrator.time, "time", lambda: now)
    state.put_instance(
        {
            "instance_id": "model#Qwen/Qwen3-32B",
            "provider_instance_id": "i-expired",
            "model": "Qwen/Qwen3-32B",
            "status": "stopped",
            "ip": "",
            "instance_type": "g5.xlarge",
            "launched_at": now - 1000,
            "last_request_at": now - 500,
            "stopped_at": now - 900,
            "warm_expires_at": now - 1,
        }
    )

    result = orchestrator.scale_up("Qwen/Qwen3-32B", state, compute)

    assert result["status"] == "starting"
    assert compute.terminated == ["i-expired"]
    assert len(compute.launched) == 1


def test_scale_down_does_not_stop_busy_instance(monkeypatch, state, compute):
    now = 10_000
    monkeypatch.setattr(orchestrator.time, "time", lambda: now)
    state.put_instance(
        {
            "instance_id": "model#Qwen/Qwen3-32B",
            "provider_instance_id": "i-busy",
            "model": "Qwen/Qwen3-32B",
            "status": "busy",
            "ip": "10.0.0.1",
            "instance_type": "g5.xlarge",
            "launched_at": now - 1000,
            "last_request_at": now - 500,
            "active_request_started_at": now - 500,
        }
    )

    result = orchestrator.scale_down(state, compute)

    assert result == {"stopping": [], "stopped": [], "terminated": []}
    assert compute.stopped == []
    assert state.get_instance("model#Qwen/Qwen3-32B")["status"] == "busy"


def test_scale_down_clears_stale_busy_marker(monkeypatch, state, compute):
    now = 10_000
    monkeypatch.setattr(orchestrator.time, "time", lambda: now)
    state.put_instance(
        {
            "instance_id": "model#Qwen/Qwen3-32B",
            "provider_instance_id": "i-busy",
            "model": "Qwen/Qwen3-32B",
            "status": "busy",
            "ip": "10.0.0.1",
            "instance_type": "g5.xlarge",
            "launched_at": now - 2000,
            "last_request_at": now - 2000,
            "active_request_starts": {
                f"{now - orchestrator.DEFAULT_MAX_REQUEST_SECONDS - 1}:request-a",
            },
        }
    )

    result = orchestrator.scale_down(state, compute)

    assert result == {"stopping": [], "stopped": [], "terminated": []}
    inst = state.get_instance("model#Qwen/Qwen3-32B")
    assert inst["status"] == "ready"
    assert inst["last_request_at"] == now
    assert "active_request_starts" not in inst


def test_scale_down_keeps_busy_when_any_request_is_still_active(monkeypatch, state, compute):
    now = 10_000
    monkeypatch.setattr(orchestrator.time, "time", lambda: now)
    state.put_instance(
        {
            "instance_id": "model#Qwen/Qwen3-32B",
            "provider_instance_id": "i-busy",
            "model": "Qwen/Qwen3-32B",
            "status": "busy",
            "ip": "10.0.0.1",
            "instance_type": "g5.xlarge",
            "launched_at": now - 2000,
            "last_request_at": now - 2000,
            "active_request_starts": {
                f"{now - orchestrator.DEFAULT_MAX_REQUEST_SECONDS - 1}:request-a",
                f"{now - 10}:request-b",
            },
        }
    )

    result = orchestrator.scale_down(state, compute)

    assert result == {"stopping": [], "stopped": [], "terminated": []}
    assert state.get_instance("model#Qwen/Qwen3-32B")["status"] == "busy"
