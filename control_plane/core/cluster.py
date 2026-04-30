"""Cluster state and manual scaling core logic."""

from __future__ import annotations

from control_plane.core.interfaces import StateStore


def get_cluster_state(state: StateStore) -> dict:
    """Return summarized cluster state for all configured models."""
    models = state.list_model_configs()
    instances = state.list_instances()

    model_states = []
    for model in models:
        name = model["name"]
        model_instances = [
            inst
            for inst in instances
            if inst.get("model") == name and inst.get("status") != "terminated"
        ]
        ready_count = sum(1 for inst in model_instances if inst.get("status") in {"ready", "busy"})
        starting_count = sum(
            1 for inst in model_instances if inst.get("status") in {"starting", "draining", "stopping"}
        )
        warm_count = sum(1 for inst in model_instances if inst.get("status") == "stopped")

        if ready_count > 0:
            status = "ready"
        elif starting_count > 0:
            status = "warming"
        elif warm_count > 0:
            status = "warm"
        else:
            status = "cold"

        model_states.append(
            {
                "name": name,
                "instance_type": model.get("instance_type"),
                "idle_timeout": model.get("idle_timeout"),
                "status": status,
                "ready_count": ready_count,
                "starting_count": starting_count,
                "warm_count": warm_count,
                "instance_count": len(model_instances),
            }
        )

    return {
        "models": model_states,
        "instances": [inst for inst in instances if inst.get("status") != "terminated"],
    }


def manual_scale(
    model: str,
    action: str,
    state: StateStore,
    trigger_scale_up,
) -> dict:
    """Manually request a scale action for a model."""
    if not model:
        raise ValueError("model is required")

    model_config = state.get_model_config(model)
    if model_config is None:
        raise ValueError(f"Unknown model: {model}")

    normalized_action = action.lower().strip()

    if normalized_action == "up":
        trigger_scale_up(model)
        return {
            "ok": True,
            "model": model,
            "action": "up",
            "message": "scale-up requested",
        }

    if normalized_action == "down":
        candidates = state.list_instances(model=model, status="ready")
        if not candidates:
            candidates = state.list_instances(model=model, status="starting")
        if not candidates:
            return {
                "ok": True,
                "model": model,
                "action": "down",
                "message": "no running instances",
            }

        target = candidates[0]
        state.update_instance(target["instance_id"], status="terminated")
        return {
            "ok": True,
            "model": model,
            "action": "down",
            "terminated_instance_id": target["instance_id"],
        }

    raise ValueError(f"Unsupported scale action: {action}")
