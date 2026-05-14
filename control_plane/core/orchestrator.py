"""Orchestrator — manages GPU instance lifecycle.

Cloud-agnostic: depends on StateStore and ComputeBackend protocols.
"""

from __future__ import annotations

import logging
import hashlib
import json
import time

import requests

from control_plane.core.interfaces import ComputeBackend, StateStore

logger = logging.getLogger(__name__)

SERVER_PORT = 8000

# Maximum time (seconds) to wait for an instance to become healthy before terminating.
MAX_START_SECONDS = 1200  # 20 minutes
DEFAULT_IDLE_TIMEOUT_SECONDS = 300
DEFAULT_WARM_TIMEOUT_SECONDS = 8 * 60 * 60  # 8 hours
DEFAULT_MAX_REQUEST_SECONDS = 20 * 60
STOPPING_RECOVERY_SECONDS = 5 * 60


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
    # Idempotency: skip if already starting or ready
    existing = state.list_instances(model=model_name, status="starting")
    existing += state.list_instances(model=model_name, status="ready")
    existing += state.list_instances(model=model_name, status="busy")
    if existing:
        logger.info("Instance already exists for %s: %s", model_name, existing[0]["instance_id"])
        return existing[0]

    model_config = state.get_model_config(model_name)
    if model_config is None:
        raise ValueError(f"Unknown model: {model_name}")

    now = int(time.time())
    launch_config_hash = _launch_config_hash(model_config, compute)

    stopping = _reconcile_stopping_for_scale_up(model_name, state, compute, now)
    if stopping is not None:
        return stopping

    stopped = state.list_instances(model=model_name, status="stopped")
    if stopped:
        warm = stopped[0]
        if _warm_instance_expired(warm, now) or _launch_config_changed(
            warm, launch_config_hash
        ):
            provider_id = warm.get("provider_instance_id")
            if provider_id:
                compute.terminate(provider_id)
            state.update_instance(warm["instance_id"], status="terminated")
        else:
            provider_id = warm.get("provider_instance_id")
            if not provider_id:
                state.update_instance(warm["instance_id"], status="terminated")
            else:
                logger.info("Starting warm instance %s for model %s", provider_id, model_name)
                state.update_instance(
                    warm["instance_id"],
                    status="starting",
                    ip="",
                    launched_at=now,
                    last_request_at=now,
                    started_at=now,
                    launch_config_hash=launch_config_hash,
                )
                try:
                    ip = compute.start(provider_id)
                except Exception:
                    logger.exception("Failed to start warm instance %s", provider_id)
                    state.update_instance(warm["instance_id"], status="stopped")
                    raise
                state.update_instance(warm["instance_id"], ip=ip)
                warm.update(
                    {
                        "status": "starting",
                        "ip": ip,
                        "launched_at": now,
                        "last_request_at": now,
                        "started_at": now,
                        "launch_config_hash": launch_config_hash,
                    }
                )
                return warm

    # Clean up any stale terminated record so put_instance_if_absent can succeed.
    stale = state.list_instances(model=model_name, status="terminated")
    for inst in stale:
        state.delete_instance(inst["instance_id"])

    placeholder_id = f"model#{model_name}"
    placeholder = {
        "instance_id": placeholder_id,
        "model": model_name,
        "status": "starting",
        "ip": "",
        "instance_type": model_config["instance_type"],
        "launched_at": now,
        "last_request_at": now,
        "launch_config_hash": launch_config_hash,
    }

    # Optimistic claim for scale-up ownership via conditional write.
    claimed = state.put_instance_if_absent(placeholder)
    if not claimed:
        existing = state.list_instances(model=model_name, status="starting")
        existing += state.list_instances(model=model_name, status="ready")
        existing += state.list_instances(model=model_name, status="busy")
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

    # Return immediately — EventBridge polls /health every minute via check_health().
    logger.info(
        "Instance %s launching for model %s; health will be checked by EventBridge",
        provider_instance_id,
        model_name,
    )
    return placeholder


def _reconcile_stopping_for_scale_up(
    model_name: str,
    state: StateStore,
    compute: ComputeBackend,
    now: int,
) -> dict | None:
    stopping = state.list_instances(model=model_name, status="stopping")
    if not stopping:
        return None

    return _reconcile_stopping_instance(
        stopping[0],
        state,
        compute,
        now,
        touch_request=True,
        recover_stale=False,
    )


def scale_down(state: StateStore, compute: ComputeBackend) -> dict[str, list[str]]:
    """Move idle instances to warm state, then terminate expired warm instances."""
    results: dict[str, list[str]] = {"stopping": [], "stopped": [], "terminated": []}
    now = int(time.time())

    _recover_stopping_instances(state, compute, now)

    ready_instances = state.list_instances(status="ready")
    for inst in ready_instances:
        model_config = state.get_model_config(inst["model"])
        idle_timeout = DEFAULT_IDLE_TIMEOUT_SECONDS
        warm_timeout = DEFAULT_WARM_TIMEOUT_SECONDS
        if model_config:
            idle_timeout = int(model_config.get("idle_timeout", DEFAULT_IDLE_TIMEOUT_SECONDS))
            warm_timeout = int(model_config.get("warm_timeout", DEFAULT_WARM_TIMEOUT_SECONDS))

        last_request = int(inst.get("last_request_at", inst.get("launched_at", 0)))
        if now - last_request > idle_timeout:
            provider_id = inst.get("provider_instance_id", inst["instance_id"])
            provider_status = {}
            try:
                provider_status = compute.instance_status(provider_id)
            except Exception:
                logger.exception("Failed to inspect idle instance %s", provider_id)

            provider_state = provider_status.get("state", "")
            if provider_state == "stopped":
                state.update_instance(
                    inst["instance_id"],
                    status="stopped",
                    previous_ip=inst.get("ip", ""),
                    ip="",
                    stopped_at=now,
                    warm_expires_at=now + warm_timeout,
                )
                results["stopped"].append(inst["instance_id"])
                continue
            if provider_state == "stopping":
                state.update_instance(
                    inst["instance_id"],
                    status="stopping",
                    stopping_at=now,
                    warm_expires_at=now + warm_timeout,
                )
                results["stopping"].append(inst["instance_id"])
                continue

            if warm_timeout > 0:
                logger.info(
                    "Stopping idle instance %s (model=%s, idle=%ds, warm_timeout=%ds)",
                    inst["instance_id"],
                    inst["model"],
                    now - last_request,
                    warm_timeout,
                )
                state.update_instance(
                    inst["instance_id"],
                    status="stopping",
                    stopping_at=now,
                    warm_expires_at=now + warm_timeout,
                )
                try:
                    compute.stop(provider_id)
                except Exception:
                    logger.exception("Failed to stop idle instance %s", provider_id)
                    state.update_instance(
                        inst["instance_id"],
                        status="ready",
                        last_request_at=now,
                        stop_error_at=now,
                    )
                    raise
                results["stopping"].append(inst["instance_id"])
            else:
                logger.info(
                    "Terminating idle instance %s (model=%s, idle=%ds)",
                    inst["instance_id"],
                    inst["model"],
                    now - last_request,
                )
                state.update_instance(inst["instance_id"], status="draining")
                compute.terminate(provider_id)
                state.update_instance(inst["instance_id"], status="terminated")
                results["terminated"].append(inst["instance_id"])

    for inst in state.list_instances(status="busy"):
        model_config = state.get_model_config(inst["model"])
        max_request_seconds = DEFAULT_MAX_REQUEST_SECONDS
        if model_config:
            max_request_seconds = int(
                model_config.get("max_request_seconds", DEFAULT_MAX_REQUEST_SECONDS)
            )
        active_since = _oldest_active_request_start(inst, now)
        if _active_requests_expired(inst, now, max_request_seconds):
            logger.warning(
                "Clearing stale active request marker for %s (model=%s, active=%ds)",
                inst["instance_id"],
                inst.get("model"),
                now - active_since,
            )
            state.update_instance(
                inst["instance_id"],
                status="ready",
                last_request_at=now,
            )
            state.remove_instance_fields(inst["instance_id"], "active_request_starts")

    for inst in state.list_instances(status="stopped"):
        if not _warm_instance_expired(inst, now):
            continue
        provider_id = inst.get("provider_instance_id")
        logger.info("Terminating expired warm instance %s (model=%s)", inst["instance_id"], inst.get("model"))
        if provider_id:
            compute.terminate(provider_id)
        state.update_instance(inst["instance_id"], status="terminated")
        results["terminated"].append(inst["instance_id"])

    return results


def _recover_stopping_instances(state: StateStore, compute: ComputeBackend, now: int) -> None:
    for inst in state.list_instances(status="stopping"):
        _reconcile_stopping_instance(
            inst,
            state,
            compute,
            now,
            touch_request=False,
            recover_stale=True,
        )


def _reconcile_stopping_instance(
    inst: dict,
    state: StateStore,
    compute: ComputeBackend,
    now: int,
    *,
    touch_request: bool,
    recover_stale: bool,
) -> dict | None:
    provider_id = inst.get("provider_instance_id")
    if not provider_id:
        state.update_instance(inst["instance_id"], status="terminated")
        return None

    try:
        provider_status = compute.instance_status(provider_id)
    except Exception:
        logger.exception("Failed to inspect stopping instance %s", provider_id)
        if touch_request:
            state.update_instance(inst["instance_id"], last_request_at=now)
            inst["last_request_at"] = now
            return inst
        return None

    provider_state = provider_status.get("state", "")
    if provider_state == "stopped":
        state.update_instance(
            inst["instance_id"],
            status="stopped",
            previous_ip=inst.get("ip", ""),
            ip="",
            stopped_at=now,
        )
        return None

    if provider_state in {"terminated", "shutting-down"}:
        state.update_instance(inst["instance_id"], status="terminated")
        return None

    if provider_state == "running" and touch_request:
        ip = provider_status.get("ip") or inst.get("ip", "")
        state.update_instance(
            inst["instance_id"],
            status="ready",
            ip=ip,
            last_request_at=now,
        )
        inst.update({"status": "ready", "ip": ip, "last_request_at": now})
        return inst

    if recover_stale and _stopping_instance_stale(inst, now):
        logger.warning(
            "Recovering stale stopping instance %s with provider state %s",
            inst["instance_id"],
            provider_state,
        )
        state.update_instance(inst["instance_id"], status="ready", last_request_at=now)
        inst.update({"status": "ready", "last_request_at": now})
        return inst

    if touch_request:
        state.update_instance(inst["instance_id"], last_request_at=now)
        inst["last_request_at"] = now
        return inst

    return None


def _stopping_instance_stale(inst: dict, now: int) -> bool:
    return now - int(inst.get("stopping_at", now)) > STOPPING_RECOVERY_SECONDS


def _warm_instance_expired(inst: dict, now: int) -> bool:
    expires_at = int(inst.get("warm_expires_at", 0))
    return expires_at > 0 and now >= expires_at


def _launch_config_changed(inst: dict, expected_hash: str) -> bool:
    existing = inst.get("launch_config_hash")
    if existing is None:
        # Instance predates fingerprinting — assume compatible, hash will be set on start.
        return False
    return existing != expected_hash


def _launch_config_hash(model_config: dict, compute: ComputeBackend) -> str:
    relevant_config = {
        "name": model_config.get("name", ""),
        "model_id": model_config.get("model_id", ""),
        "instance_type": model_config.get("instance_type", ""),
        "vllm_args": model_config.get("vllm_args", ""),
        "s3_key": model_config.get("s3_key", ""),
    }
    runtime_fingerprint = getattr(compute, "runtime_fingerprint", lambda: "")()
    payload = {
        "model": relevant_config,
        "runtime": runtime_fingerprint,
    }
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _oldest_active_request_start(inst: dict, default: int) -> int:
    starts = inst.get("active_request_starts")
    if not starts:
        return int(inst.get("active_request_started_at", inst.get("last_request_at", default)))

    parsed = []
    for token in starts:
        try:
            parsed.append(int(str(token).split(":", 1)[0]))
        except (TypeError, ValueError):
            continue
    return min(parsed) if parsed else default


def _active_requests_expired(inst: dict, now: int, max_request_seconds: int) -> bool:
    starts = inst.get("active_request_starts")
    if not starts:
        active_since = int(inst.get("active_request_started_at", inst.get("last_request_at", now)))
        return now - active_since > max_request_seconds

    parsed = []
    for token in starts:
        try:
            parsed.append(int(str(token).split(":", 1)[0]))
        except (TypeError, ValueError):
            continue
    return bool(parsed) and all(now - start > max_request_seconds for start in parsed)


def check_health(
    state: StateStore,
    compute: ComputeBackend,
    api_key: str = "",
) -> dict:
    """Check health of all 'starting' instances (called by EventBridge every minute).

    For each starting instance:
    - If healthy → mark 'ready'.
    - If older than MAX_START_SECONDS with no response → terminate.

    Returns counts of what happened.
    """
    now = int(time.time())
    results: dict[str, list] = {"became_ready": [], "terminated": [], "still_starting": []}

    for inst in state.list_instances(status="starting"):
        instance_id = inst["instance_id"]
        provider_id = inst.get("provider_instance_id", "")
        ip = inst.get("ip", "")
        age = now - int(inst.get("launched_at", now))

        if age > MAX_START_SECONDS:
            logger.error(
                "Instance %s (model=%s) timed out after %ds, terminating",
                instance_id,
                inst.get("model"),
                age,
            )
            if provider_id:
                try:
                    compute.terminate(provider_id)
                except Exception:
                    logger.exception("Failed to terminate timed-out instance %s", provider_id)
            state.update_instance(instance_id, status="terminated")
            results["terminated"].append(instance_id)
            continue

        if not ip:
            results["still_starting"].append(instance_id)
            continue

        # Single quick probe — don't block; EventBridge retries next minute.
        try:
            url = f"http://{ip}:{SERVER_PORT}/health"
            headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
            resp = requests.get(url, headers=headers, timeout=5)
            if resp.status_code == 200:
                state.update_instance(instance_id, status="ready", last_request_at=now)
                logger.info("Instance %s (model=%s) is now ready", instance_id, inst.get("model"))
                results["became_ready"].append(instance_id)
            else:
                logger.debug("Instance %s health returned %s", instance_id, resp.status_code)
                results["still_starting"].append(instance_id)
        except requests.RequestException:
            results["still_starting"].append(instance_id)

    return results
