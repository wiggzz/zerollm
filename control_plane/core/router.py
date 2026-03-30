"""Router core logic for OpenAI-compatible inference APIs."""

from __future__ import annotations

import logging
import time
from typing import Any

import requests

from control_plane.core.interfaces import StateStore
from control_plane.shared.config import normalize_model_name

logger = logging.getLogger(__name__)

VLLM_PORT = 8000


def proxy_request(
    ip: str, port: int, path: str, body: dict, api_key: str = ""
) -> tuple[int, dict[str, Any] | str, dict[str, str]]:
    """Forward an inference request to a vLLM instance."""
    url = f"http://{ip}:{port}{path}"
    headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
    response = requests.post(url, json=body, headers=headers, timeout=120)
    content_type = response.headers.get("Content-Type", "")
    if content_type.lower().startswith("text/event-stream"):
        return response.status_code, response.text, {"Content-Type": content_type}
    return response.status_code, response.json(), {}


def handle_inference(
    model: str,
    body: dict,
    state: StateStore,
    trigger_scale_up,
    path: str = "/v1/chat/completions",
    vllm_api_key: str = "",
) -> dict:
    """Route an inference request to a ready instance or trigger scale-up.

    Returns a normalized response payload consumed by Lambda handlers.
    """
    if not model:
        return {
            "status_code": 400,
            "body": {"error": {"message": "model is required", "type": "invalid_request_error"}},
        }

    model = normalize_model_name(model)
    ready_instances = state.list_instances(model=model, status="ready")
    if not ready_instances:
        logger.info("No ready instance for model=%s, triggering scale-up", model)
        trigger_scale_up(model)
        return {
            "status_code": 503,
            "body": {
                "error": {
                    "message": "Model is cold-starting. Retry shortly.",
                    "type": "service_unavailable",
                }
            },
            "headers": {"Retry-After": "10"},
        }

    target = ready_instances[0]
    state.update_instance(target["instance_id"], last_request_at=int(time.time()))

    try:
        status, payload, headers = proxy_request(
            target["ip"], VLLM_PORT, path, body, api_key=vllm_api_key
        )
        result = {"status_code": status, "body": payload}
        if headers:
            result["headers"] = headers
        return result
    except requests.RequestException as exc:
        logger.exception("Proxy request failed for instance=%s", target["instance_id"])
        return {
            "status_code": 502,
            "body": {
                "error": {
                    "message": f"Upstream inference server unavailable: {exc}",
                    "type": "bad_gateway",
                }
            },
        }


def list_models(state: StateStore) -> dict:
    """Return configured model catalog in OpenAI-compatible format."""
    models = state.list_model_configs()
    return {
        "object": "list",
        "data": [
            {
                "id": model["name"],
                "object": "model",
                "owned_by": "diogenes",
            }
            for model in models
        ],
    }
