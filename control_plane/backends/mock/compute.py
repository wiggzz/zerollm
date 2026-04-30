"""Mock compute backend for testing."""

from __future__ import annotations

import uuid


class MockComputeBackend:
    """Simulates launching/terminating instances.

    In tests, the mock vLLM server runs on localhost. The 'ip' returned by
    launch() points there so the router can proxy to it.
    """

    def __init__(self, mock_ip: str = "127.0.0.1"):
        self.mock_ip = mock_ip
        self.launched: list[dict] = []
        self.started: list[str] = []
        self.stopped: list[str] = []
        self.terminated: list[str] = []
        self.instance_states: dict[str, str] = {}

    def launch(self, model_config: dict) -> tuple[str, str]:
        instance_id = f"i-mock-{uuid.uuid4().hex[:8]}"
        self.launched.append(
            {"instance_id": instance_id, "model_config": model_config}
        )
        self.instance_states[instance_id] = "running"
        return instance_id, self.mock_ip

    def start(self, instance_id: str) -> str:
        self.started.append(instance_id)
        self.instance_states[instance_id] = "running"
        return self.mock_ip

    def stop(self, instance_id: str) -> None:
        self.stopped.append(instance_id)
        self.instance_states[instance_id] = "stopped"

    def instance_status(self, instance_id: str) -> dict:
        return {
            "state": self.instance_states.get(instance_id, "running"),
            "ip": self.mock_ip,
        }

    def terminate(self, instance_id: str) -> None:
        self.terminated.append(instance_id)
        self.instance_states[instance_id] = "terminated"
