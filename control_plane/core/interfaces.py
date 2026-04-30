"""Abstract interfaces for Diogenes backends.

Core business logic depends only on these protocols, never on cloud-specific
SDKs like boto3. To add a new cloud backend, implement these protocols and
wire them up in a thin handler layer.
"""

from __future__ import annotations

from typing import Protocol


class StateStore(Protocol):
    """Persistent state for instances, models, and API keys."""

    # --- Instances ---

    def get_instance(self, instance_id: str) -> dict | None:
        """Get a single instance by ID."""
        ...

    def list_instances(
        self, *, model: str | None = None, status: str | None = None
    ) -> list[dict]:
        """List instances, optionally filtered by model and/or status."""
        ...

    def put_instance(self, instance: dict) -> None:
        """Create or overwrite an instance record."""
        ...

    def update_instance(self, instance_id: str, **fields) -> None:
        """Update specific fields on an instance record."""
        ...

    def remove_instance_fields(self, instance_id: str, *fields: str) -> None:
        """Remove fields from an instance record."""
        ...

    def put_instance_if_absent(self, instance: dict) -> bool:
        """Create an instance record if its primary key does not already exist."""
        ...

    def delete_instance(self, instance_id: str) -> None:
        """Delete an instance record by ID."""
        ...

    # --- Models ---

    def get_model_config(self, model_name: str) -> dict | None:
        """Get configuration for a model by name."""
        ...

    def list_model_configs(self) -> list[dict]:
        """List all configured models."""
        ...

    # --- API Keys ---

    def get_api_key(self, key_hash: str) -> dict | None:
        """Look up an API key record by its SHA-256 hash."""
        ...

    def put_api_key(self, key: dict) -> None:
        """Store an API key record."""
        ...

    def delete_api_key(self, key_hash: str) -> None:
        """Delete an API key by its hash."""
        ...

    def list_api_keys(self, email: str) -> list[dict]:
        """List all API keys belonging to an email."""
        ...


class ComputeBackend(Protocol):
    """Launch, stop, start, and terminate GPU instances."""

    def launch(self, model_config: dict) -> tuple[str, str]:
        """Launch an instance for the given model config.

        Returns (instance_id, ip_address).
        """
        ...

    def start(self, instance_id: str) -> str:
        """Start a stopped instance and return its current IP address."""
        ...

    def stop(self, instance_id: str) -> None:
        """Stop an instance by ID while preserving its EBS volumes."""
        ...

    def instance_status(self, instance_id: str) -> dict:
        """Return provider instance status fields such as state and IP address."""
        ...

    def terminate(self, instance_id: str) -> None:
        """Terminate an instance by ID."""
        ...
