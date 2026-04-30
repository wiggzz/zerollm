"""DynamoDB-backed state store."""

from __future__ import annotations

import boto3
from boto3.dynamodb.conditions import Key, Attr


class DynamoDBStateStore:
    def __init__(
        self,
        instances_table: str,
        models_table: str,
        api_keys_table: str,
        endpoint_url: str | None = None,
        region_name: str | None = None,
    ):
        kwargs = {}
        if endpoint_url:
            kwargs["endpoint_url"] = endpoint_url
        if region_name:
            kwargs["region_name"] = region_name
        dynamodb = boto3.resource("dynamodb", **kwargs)
        self._instances = dynamodb.Table(instances_table)
        self._models = dynamodb.Table(models_table)
        self._api_keys = dynamodb.Table(api_keys_table)

    # --- Instances ---

    def get_instance(self, instance_id: str) -> dict | None:
        resp = self._instances.get_item(Key={"instance_id": instance_id})
        return resp.get("Item")

    def list_instances(
        self, *, model: str | None = None, status: str | None = None
    ) -> list[dict]:
        if model is not None and status is not None:
            resp = self._instances.query(
                IndexName="model-status-index",
                KeyConditionExpression=Key("model").eq(model)
                & Key("status").eq(status),
            )
            return resp.get("Items", [])

        if model is not None:
            resp = self._instances.query(
                IndexName="model-status-index",
                KeyConditionExpression=Key("model").eq(model),
            )
            return resp.get("Items", [])

        if status is not None:
            resp = self._instances.scan(
                FilterExpression=Attr("status").eq(status),
            )
            return resp.get("Items", [])

        resp = self._instances.scan()
        return resp.get("Items", [])

    def put_instance(self, instance: dict) -> None:
        self._instances.put_item(Item=instance)

    def update_instance(self, instance_id: str, **fields) -> None:
        update_parts = []
        values = {}
        names = {}
        for i, (k, v) in enumerate(fields.items()):
            placeholder = f":v{i}"
            name_placeholder = f"#n{i}"
            update_parts.append(f"{name_placeholder} = {placeholder}")
            values[placeholder] = v
            names[name_placeholder] = k

        self._instances.update_item(
            Key={"instance_id": instance_id},
            UpdateExpression="SET " + ", ".join(update_parts),
            ExpressionAttributeValues=values,
            ExpressionAttributeNames=names,
        )


    def remove_instance_fields(self, instance_id: str, *fields: str) -> None:
        if not fields:
            return

        names = {f"#n{i}": field for i, field in enumerate(fields)}
        self._instances.update_item(
            Key={"instance_id": instance_id},
            UpdateExpression="REMOVE " + ", ".join(names.keys()),
            ExpressionAttributeNames=names,
        )


    def delete_instance(self, instance_id: str) -> None:
        self._instances.delete_item(Key={"instance_id": instance_id})

    def put_instance_if_absent(self, instance: dict) -> bool:
        try:
            self._instances.put_item(
                Item=instance,
                ConditionExpression="attribute_not_exists(instance_id)",
            )
            return True
        except self._instances.meta.client.exceptions.ConditionalCheckFailedException:
            return False


    # --- Models ---

    def get_model_config(self, model_name: str) -> dict | None:
        resp = self._models.get_item(Key={"name": model_name})
        return resp.get("Item")

    def list_model_configs(self) -> list[dict]:
        resp = self._models.scan()
        return resp.get("Items", [])

    # --- API Keys ---

    def get_api_key(self, key_hash: str) -> dict | None:
        resp = self._api_keys.get_item(Key={"key_hash": key_hash})
        return resp.get("Item")

    def put_api_key(self, key: dict) -> None:
        self._api_keys.put_item(Item=key)

    def delete_api_key(self, key_hash: str) -> None:
        self._api_keys.delete_item(Key={"key_hash": key_hash})

    def list_api_keys(self, email: str) -> list[dict]:
        resp = self._api_keys.query(
            IndexName="email-index",
            KeyConditionExpression=Key("email").eq(email),
        )
        return resp.get("Items", [])
