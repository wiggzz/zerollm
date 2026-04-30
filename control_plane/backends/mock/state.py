"""In-memory state store for testing."""

from __future__ import annotations


class InMemoryStateStore:
    def __init__(self):
        self._instances: dict[str, dict] = {}
        self._models: dict[str, dict] = {}
        self._api_keys: dict[str, dict] = {}

    # --- Instances ---

    def get_instance(self, instance_id: str) -> dict | None:
        return self._instances.get(instance_id)

    def list_instances(
        self, *, model: str | None = None, status: str | None = None
    ) -> list[dict]:
        results = list(self._instances.values())
        if model is not None:
            results = [i for i in results if i.get("model") == model]
        if status is not None:
            results = [i for i in results if i.get("status") == status]
        return results

    def put_instance(self, instance: dict) -> None:
        self._instances[instance["instance_id"]] = instance

    def update_instance(self, instance_id: str, **fields) -> None:
        inst = self._instances.get(instance_id)
        if inst is None:
            raise KeyError(f"Instance {instance_id} not found")
        inst.update(fields)

    def remove_instance_fields(self, instance_id: str, *fields: str) -> None:
        inst = self._instances.get(instance_id)
        if inst is None:
            raise KeyError(f"Instance {instance_id} not found")
        for field in fields:
            inst.pop(field, None)

    def put_instance_if_absent(self, instance: dict) -> bool:
        instance_id = instance["instance_id"]
        if instance_id in self._instances:
            return False
        self._instances[instance_id] = instance
        return True

    def delete_instance(self, instance_id: str) -> None:
        self._instances.pop(instance_id, None)

    # --- Models ---

    def get_model_config(self, model_name: str) -> dict | None:
        return self._models.get(model_name)

    def list_model_configs(self) -> list[dict]:
        return list(self._models.values())

    def put_model_config(self, config: dict) -> None:
        self._models[config["name"]] = config

    # --- API Keys ---

    def get_api_key(self, key_hash: str) -> dict | None:
        return self._api_keys.get(key_hash)

    def put_api_key(self, key: dict) -> None:
        self._api_keys[key["key_hash"]] = key

    def delete_api_key(self, key_hash: str) -> None:
        self._api_keys.pop(key_hash, None)

    def list_api_keys(self, email: str) -> list[dict]:
        return [k for k in self._api_keys.values() if k.get("email") == email]
