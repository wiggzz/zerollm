"""Orchestrator — manages GPU instance lifecycle.

Cloud-agnostic: depends on StateStore and ComputeBackend protocols.
"""

from __future__ import annotations

import logging
import time

import requests

from control_plane.core.interfaces import ComputeBackend, StateStore
from control_plane.shared.config import normalize_model_name

logger = logging.getLogger(__name__)

VLLM_PORT = 8000


def scale_up(
    model_name: str,
    state: StateStore,
    compute: ComputeBackend,
    vllm_api_key: str = "",
) -> dict:
    """Launch a GPU instance for the given model (idempotent).

    Uses optimistic write semantics on a single per-model placeholder row,
    avoiding explicit acquire/release lock steps.

    Returns the instance record.
    """
    model_name = normalize_model_name(model_name)
    # Idempotency: skip if already starting or ready
    existing = state.list_instances(model=model_name, status="starting")
    existing += state.list_instances(model=model_name, status="ready")
    if existing:
        logger.info("Instance already exists for %s: %s", model_name, existing[0]["instance_id"])
        return existing[0]

    # Clean up any stale terminated record so put_instance_if_absent can succeed.
    stale = state.list_instances(model=model_name, status="terminated")
    for inst in stale:
        state.delete_instance(inst["instance_id"])

    model_config = state.get_model_config(model_name)
    if model_config is None:
        raise ValueError(f"Unknown model: {model_name}")

    now = int(time.time())
    placeholder_id = f"model#{model_name}"
    placeholder = {
        "instance_id": placeholder_id,
        "model": model_name,
        "status": "starting",
        "ip": "",
        "instance_type": model_config["instance_type"],
        "launched_at": now,
        "last_request_at": now,
    }

    # Optimistic claim for scale-up ownership via conditional write.
    claimed = state.put_instance_if_absent(placeholder)
    if not claimed:
        existing = state.list_instances(model=model_name, status="starting")
        existing += state.list_instances(model=model_name, status="ready")
        if existing:
            return existing[0]
        return placeholder

    # Launch instance after successful claim.
    logger.info("Launching instance for model %s", model_name)
    try:
        provider_instance_id, ip = compute.launch(model_config)
    except Exception:
        logger.exception("Failed to launch instance for model %s, cleaning up placeholder", model_name)
        state.update_instance(placeholder_id, status="terminated")
        raise

    state.update_instance(
        placeholder_id,
        provider_instance_id=provider_instance_id,
        ip=ip,
    )
    placeholder["provider_instance_id"] = provider_instance_id
    placeholder["ip"] = ip

    try:
        healthy = poll_health(ip, VLLM_PORT, timeout=860, api_key=vllm_api_key)
    except Exception:
        logger.exception("poll_health raised for %s, terminating", provider_instance_id)
        healthy = False

    if healthy:
        state.update_instance(placeholder_id, status="ready", last_request_at=int(time.time()))
        placeholder["status"] = "ready"
        logger.info("Instance %s is ready", provider_instance_id)
    else:
        logger.error("Instance %s failed health check, terminating", provider_instance_id)
        compute.terminate(provider_instance_id)
        state.update_instance(placeholder_id, status="terminated")
        placeholder["status"] = "terminated"

    return placeholder


def scale_down(
    state: StateStore,
    compute: ComputeBackend,
) -> list[str]:
    """Terminate idle instances past their idle timeout.

    Returns list of terminated instance IDs.
    """
    terminated = []
    now = int(time.time())

    ready_instances = state.list_instances(status="ready")
    for inst in ready_instances:
        model_config = state.get_model_config(inst["model"])
        idle_timeout = 300  # default
        if model_config:
            idle_timeout = int(model_config.get("idle_timeout", 300))

        last_request = int(inst.get("last_request_at", inst.get("launched_at", 0)))
        if now - last_request > idle_timeout:
            logger.info(
                "Terminating idle instance %s (model=%s, idle=%ds)",
                inst["instance_id"],
                inst["model"],
                now - last_request,
            )
            state.update_instance(inst["instance_id"], status="draining")
            compute.terminate(inst.get("provider_instance_id", inst["instance_id"]))
            state.update_instance(inst["instance_id"], status="terminated")
            terminated.append(inst["instance_id"])

    return terminated


def poll_health(
    ip: str,
    port: int = VLLM_PORT,
    timeout: int = 600,
    interval: int = 10,
    api_key: str = "",
) -> bool:
    """Poll an instance's health endpoint until it responds 200 or times out."""
    url = f"http://{ip}:{port}/health"
    headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
    deadline = time.time() + timeout

    while time.time() < deadline:
        try:
            resp = requests.get(url, headers=headers, timeout=5)
            if resp.status_code == 200:
                return True
            logger.debug("poll_health got %s from %s", resp.status_code, url)
        except requests.RequestException:
            pass
        time.sleep(interval)

    return False
