"""Smoke tests for mock backends — validates Phase 0 scaffolding."""


def test_state_store_instance_crud(state):
    instance = {
        "instance_id": "i-test123",
        "model": "Qwen/Qwen3-32B",
        "status": "starting",
        "ip": "10.0.0.1",
    }
    state.put_instance(instance)

    got = state.get_instance("i-test123")
    assert got is not None
    assert got["status"] == "starting"

    state.update_instance("i-test123", status="ready")
    got = state.get_instance("i-test123")
    assert got["status"] == "ready"

    instances = state.list_instances(model="Qwen/Qwen3-32B", status="ready")
    assert len(instances) == 1


def test_state_store_model_config(state):
    config = state.get_model_config("Qwen/Qwen3-32B")
    assert config is not None
    assert config["instance_type"] == "g5.xlarge"

    all_models = state.list_model_configs()
    assert len(all_models) == 1


def test_state_store_api_key_crud(state):
    key = {
        "key_hash": "abc123hash",
        "email": "test@example.com",
        "name": "laptop",
        "created_at": 1700000000,
    }
    state.put_api_key(key)

    got = state.get_api_key("abc123hash")
    assert got is not None
    assert got["email"] == "test@example.com"

    keys = state.list_api_keys("test@example.com")
    assert len(keys) == 1

    state.delete_api_key("abc123hash")
    assert state.get_api_key("abc123hash") is None


def test_compute_backend_launch_and_terminate(compute):
    model_config = {"name": "test-model", "instance_type": "g5.xlarge"}

    instance_id, ip = compute.launch(model_config)
    assert instance_id.startswith("i-mock-")
    assert ip == "127.0.0.1"
    assert len(compute.launched) == 1

    assert compute.start(instance_id) == "127.0.0.1"
    assert instance_id in compute.started

    compute.stop(instance_id)
    assert instance_id in compute.stopped

    compute.terminate(instance_id)
    assert instance_id in compute.terminated
