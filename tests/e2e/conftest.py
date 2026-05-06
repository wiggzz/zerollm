"""E2E test fixtures — mock vLLM plus optional LocalStack-backed DynamoDB."""

from __future__ import annotations

import os
import sys
import uuid

import boto3
import pytest
from botocore.exceptions import BotoCoreError

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from tests.e2e.mock_vllm import MockVLLMServer


@pytest.fixture(scope="session")
def mock_vllm():
    """Start a mock vLLM server on a random port."""
    server = MockVLLMServer()
    server.start()
    yield server
    server.stop()


@pytest.fixture(scope="session")
def localstack_env():
    """Start LocalStack and provision DynamoDB tables used by handlers."""
    try:
        from testcontainers.core.exceptions import ContainerStartException
        from testcontainers.localstack import LocalStackContainer
    except ModuleNotFoundError as exc:
        pytest.skip(f"LocalStack tests require testcontainers dependency: {exc}")

    container = LocalStackContainer(image="localstack/localstack:3.0").with_services("dynamodb")

    try:
        container.start()
    except (ContainerStartException, BotoCoreError, OSError) as exc:
        pytest.skip(f"LocalStack unavailable in this environment: {exc}")

    endpoint_url = container.get_url()
    region = "us-east-1"
    access_key = "test"
    secret_key = "test"

    os.environ.setdefault("AWS_ACCESS_KEY_ID", access_key)
    os.environ.setdefault("AWS_SECRET_ACCESS_KEY", secret_key)
    os.environ.setdefault("AWS_DEFAULT_REGION", region)

    suffix = uuid.uuid4().hex[:8]
    instances_table = f"zerollm-instances-e2e-{suffix}"
    models_table = f"zerollm-models-e2e-{suffix}"
    api_keys_table = f"zerollm-api-keys-e2e-{suffix}"

    dynamodb = boto3.client(
        "dynamodb",
        endpoint_url=endpoint_url,
        region_name=region,
        aws_access_key_id=access_key,
        aws_secret_access_key=secret_key,
    )

    dynamodb.create_table(
        TableName=instances_table,
        AttributeDefinitions=[
            {"AttributeName": "instance_id", "AttributeType": "S"},
            {"AttributeName": "model", "AttributeType": "S"},
            {"AttributeName": "status", "AttributeType": "S"},
        ],
        KeySchema=[{"AttributeName": "instance_id", "KeyType": "HASH"}],
        BillingMode="PAY_PER_REQUEST",
        GlobalSecondaryIndexes=[
            {
                "IndexName": "model-status-index",
                "KeySchema": [
                    {"AttributeName": "model", "KeyType": "HASH"},
                    {"AttributeName": "status", "KeyType": "RANGE"},
                ],
                "Projection": {"ProjectionType": "ALL"},
            }
        ],
    )

    dynamodb.create_table(
        TableName=models_table,
        AttributeDefinitions=[{"AttributeName": "name", "AttributeType": "S"}],
        KeySchema=[{"AttributeName": "name", "KeyType": "HASH"}],
        BillingMode="PAY_PER_REQUEST",
    )

    dynamodb.create_table(
        TableName=api_keys_table,
        AttributeDefinitions=[
            {"AttributeName": "key_hash", "AttributeType": "S"},
            {"AttributeName": "email", "AttributeType": "S"},
        ],
        KeySchema=[{"AttributeName": "key_hash", "KeyType": "HASH"}],
        BillingMode="PAY_PER_REQUEST",
        GlobalSecondaryIndexes=[
            {
                "IndexName": "email-index",
                "KeySchema": [{"AttributeName": "email", "KeyType": "HASH"}],
                "Projection": {"ProjectionType": "ALL"},
            }
        ],
    )

    waiter = dynamodb.get_waiter("table_exists")
    for table_name in (instances_table, models_table, api_keys_table):
        waiter.wait(TableName=table_name)

    yield {
        "endpoint_url": endpoint_url,
        "region": region,
        "aws_access_key_id": access_key,
        "aws_secret_access_key": secret_key,
        "instances_table": instances_table,
        "models_table": models_table,
        "api_keys_table": api_keys_table,
    }

    container.stop()
