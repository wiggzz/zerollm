"""AWS Lambda handler entry points.

These are thin wrappers that parse Lambda events, build backend dependencies,
call cloud-agnostic core logic, and format responses. All business logic
lives in control_plane/core/.
"""

from __future__ import annotations

import json
import logging
import os
from decimal import Decimal

logging.basicConfig(
    level=getattr(logging, os.environ.get("LOG_LEVEL", "INFO").upper(), logging.INFO),
    format="%(levelname)s %(name)s %(message)s",
)
logger = logging.getLogger(__name__)


# ---- Shared helpers ----

_state_store = None


def _get_state_store():
    """Return a cached DynamoDBStateStore (built once per Lambda execution environment)."""
    global _state_store
    if _state_store is None:
        from control_plane.shared.config import INSTANCES_TABLE, MODELS_TABLE, API_KEYS_TABLE
        from control_plane.backends.aws.state import DynamoDBStateStore

        _state_store = DynamoDBStateStore(
            instances_table=INSTANCES_TABLE(),
            models_table=MODELS_TABLE(),
            api_keys_table=API_KEYS_TABLE(),
        )
    return _state_store


def _get_compute_backend():
    """Build an EC2ComputeBackend from environment variables."""
    import os
    from control_plane.shared.config import get_env
    from control_plane.backends.aws.compute import EC2ComputeBackend

    return EC2ComputeBackend(
        ami_id=get_env("GPU_AMI_ID"),
        security_group_id=get_env("GPU_SECURITY_GROUP_ID"),
        subnet_id=get_env("GPU_SUBNET_ID"),
        instance_profile_arn=get_env("GPU_INSTANCE_PROFILE_ARN"),
        vllm_api_key=os.environ.get("VLLM_API_KEY", ""),
    )


def _api_response(status_code: int, body: dict | str, headers: dict | None = None) -> dict:
    """Format an API Gateway v2 response."""
    response_body = body if isinstance(body, str) else json.dumps(body, default=_json_default)
    resp = {
        "statusCode": status_code,
        "headers": {"Content-Type": "application/json"},
        "body": response_body,
    }
    if headers:
        resp["headers"].update(headers)
    return resp


def _json_default(value):
    """Serialize DynamoDB types that Python's JSON encoder does not handle."""
    if isinstance(value, Decimal):
        # Preserve integer semantics when possible (e.g. Decimal("1") -> 1).
        return int(value) if value == value.to_integral_value() else float(value)
    raise TypeError(f"Object of type {value.__class__.__name__} is not JSON serializable")


# ---- Phase 1: Orchestrator ----


def orchestrator_handler(event, context):
    """Orchestrator Lambda — handles scale-up (async invoke), scale-down, and check-health (EventBridge).

    Scale-up event:     {"action": "scale_up", "model": "Qwen/Qwen3-32B"}
    Scale-down event:   {"source": "schedule", "action": "scale_down"}
    Check-health event: {"source": "schedule", "action": "check_health"}
    """
    from control_plane.core.orchestrator import scale_up, scale_down, check_health

    state = _get_state_store()
    compute = _get_compute_backend()

    action = event.get("action", "")

    if action == "scale_up":
        import os
        model_name = event["model"]
        result = scale_up(model_name, state, compute, vllm_api_key=os.environ.get("VLLM_API_KEY", ""))
        return {"statusCode": 200, "body": json.dumps(result, default=_json_default)}

    elif action == "check_health":
        import os
        result = check_health(state, compute, api_key=os.environ.get("VLLM_API_KEY", ""))
        return {"statusCode": 200, "body": json.dumps(result, default=_json_default)}

    elif action == "scale_down" or event.get("source") in ("schedule", "aws.events"):
        terminated = scale_down(state, compute)
        return {"statusCode": 200, "body": json.dumps({"terminated": terminated}, default=_json_default)}

    else:
        logger.warning("Unknown orchestrator event: %s", event)
        return {"statusCode": 400, "body": json.dumps({"error": "unknown action"})}


# ---- Phase 2: Router ----


def router_handler(event, context):
    """Router Lambda — handles OpenAI-compatible API requests."""
    from control_plane.core.router import handle_inference, list_models

    state = _get_state_store()

    method = event.get("requestContext", {}).get("http", {}).get("method", "GET")
    path = event.get("rawPath", "")

    if method == "GET" and path == "/v1/models":
        result = list_models(state)
        return _api_response(200, result)

    if method == "POST" and path in ("/v1/chat/completions", "/v1/completions"):
        import os
        body = json.loads(event.get("body", "{}"))
        trigger = _make_trigger_scale_up()
        result = handle_inference(
            model=body.get("model", ""),
            body=body,
            path=path,
            state=state,
            trigger_scale_up=trigger,
            vllm_api_key=os.environ.get("VLLM_API_KEY", ""),
        )
        return _api_response(
            result["status_code"],
            result["body"],
            result.get("headers"),
        )

    return _api_response(404, {"error": "not found"})


def _make_trigger_scale_up():
    """Return a callable that async-invokes the Orchestrator Lambda."""
    import boto3
    from control_plane.shared.config import ORCHESTRATOR_FUNCTION_NAME

    client = boto3.client("lambda")
    function_name = ORCHESTRATOR_FUNCTION_NAME()

    def trigger(model_name: str):
        client.invoke(
            FunctionName=function_name,
            InvocationType="Event",  # async
            Payload=json.dumps({"action": "scale_up", "model": model_name}),
        )

    return trigger


# ---- Phase 3: Auth ----


def authorizer_handler(event, context):
    """Lambda Authorizer (API Gateway v2 payload format 2.0, simple response)."""
    from control_plane.core.auth import validate_api_key

    state = _get_state_store()

    headers = event.get("headers", {})
    auth_header = headers.get("authorization", "")
    token = auth_header.replace("Bearer ", "", 1) if auth_header.startswith("Bearer ") else ""

    if not token:
        return {"isAuthorized": False}

    if token.startswith("dio-"):
        authorized, email = validate_api_key(token, state)
        if authorized:
            return {"isAuthorized": True, "context": {"email": email}}
        return {"isAuthorized": False}

    # TODO Phase 5: Google JWT validation
    return {"isAuthorized": False}


def keys_handler(event, context):
    """API key CRUD Lambda."""
    from control_plane.core.keys import create_key, list_keys, delete_key

    state = _get_state_store()

    method = event.get("requestContext", {}).get("http", {}).get("method", "")
    path = event.get("rawPath", "")

    # Extract email from authorizer context
    email = (
        event.get("requestContext", {})
        .get("authorizer", {})
        .get("lambda", {})
        .get("email", "unknown@example.com")
    )

    if method == "POST" and path == "/api/keys":
        body = json.loads(event.get("body", "{}"))
        name = body.get("name", "default")
        result = create_key(email, name, state)
        return _api_response(201, result)

    if method == "GET" and path == "/api/keys":
        result = list_keys(email, state)
        return _api_response(200, {"keys": result})

    if method == "DELETE" and "/api/keys/" in path:
        key_id = event.get("pathParameters", {}).get("key_id", "")
        delete_key(key_id, email, state)
        return _api_response(200, {"deleted": True})

    return _api_response(404, {"error": "not found"})


# ---- Phase 4: Cluster State ----


def cluster_handler(event, context):
    """Cluster state Lambda."""
    from control_plane.core.cluster import get_cluster_state, manual_scale

    state = _get_state_store()

    method = event.get("requestContext", {}).get("http", {}).get("method", "")
    path = event.get("rawPath", "")

    if method == "GET" and path == "/api/cluster":
        result = get_cluster_state(state)
        return _api_response(200, result)

    if method == "POST" and path == "/api/cluster/scale":
        body = json.loads(event.get("body", "{}"))
        trigger = _make_trigger_scale_up()
        try:
            result = manual_scale(
                model=body.get("model", ""),
                action=body.get("action", "up"),
                state=state,
                trigger_scale_up=trigger,
            )
        except ValueError as exc:
            return _api_response(400, {"error": str(exc)})
        return _api_response(200, result)

    return _api_response(404, {"error": "not found"})
