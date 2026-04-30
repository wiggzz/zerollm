"""LocalStack-backed handler E2E tests for Phase 4."""

from __future__ import annotations

import json

from control_plane.backends.aws import handlers
from control_plane.backends.aws.state import DynamoDBStateStore
from control_plane.backends.mock.compute import MockComputeBackend
from control_plane.core import orchestrator


MODEL_NAME = "Qwen/Qwen3-32B"


def _build_state(localstack_env):
    return DynamoDBStateStore(
        instances_table=localstack_env["instances_table"],
        models_table=localstack_env["models_table"],
        api_keys_table=localstack_env["api_keys_table"],
        endpoint_url=localstack_env["endpoint_url"],
    )


def _seed_model(state):
    state._models.put_item(
        Item={
            "name": MODEL_NAME,
            "instance_type": "g5.xlarge",
            "idle_timeout": 1,
            "vllm_args": "--max-model-len 32768",
        }
    )


def test_localstack_cluster_and_scale_down_flow(localstack_env, mock_vllm, monkeypatch):
    state = _build_state(localstack_env)
    _seed_model(state)

    monkeypatch.setattr(handlers, "_get_state_store", lambda: state)
    monkeypatch.setattr(orchestrator, "SERVER_PORT", mock_vllm.port)

    compute = MockComputeBackend(mock_ip=mock_vllm.host)
    orchestrator.scale_up(MODEL_NAME, state, compute)
    orchestrator.check_health(state, compute)

    cluster_event = {"requestContext": {"http": {"method": "GET"}}, "rawPath": "/api/cluster"}
    cluster = handlers.cluster_handler(cluster_event, None)
    cluster_body = json.loads(cluster["body"])
    assert cluster["statusCode"] == 200
    assert cluster_body["models"][0]["name"] == MODEL_NAME
    assert cluster_body["models"][0]["status"] == "ready"

    inst = state.list_instances(model=MODEL_NAME, status="ready")[0]
    state.update_instance(inst["instance_id"], last_request_at=0)
    monkeypatch.setattr(orchestrator.time, "time", lambda: 10)

    scaled_down = orchestrator.scale_down(state, compute)
    assert scaled_down == {"stopping": [inst["instance_id"]], "stopped": [], "terminated": []}


def test_localstack_keys_and_authorizer_flow(localstack_env, monkeypatch):
    state = _build_state(localstack_env)
    monkeypatch.setattr(handlers, "_get_state_store", lambda: state)

    unauthorized = handlers.authorizer_handler({"headers": {}}, None)
    assert unauthorized == {"isAuthorized": False}

    create_event = {
        "requestContext": {"http": {"method": "POST"}, "authorizer": {"lambda": {"email": "owner@example.com"}}},
        "rawPath": "/api/keys",
        "body": json.dumps({"name": "localstack"}),
    }
    create_resp = handlers.keys_handler(create_event, None)
    create_body = json.loads(create_resp["body"])

    auth_event = {"headers": {"authorization": f"Bearer {create_body['key']}"}}
    authorized = handlers.authorizer_handler(auth_event, None)
    assert authorized["isAuthorized"] is True
    assert authorized["context"]["email"] == "owner@example.com"
