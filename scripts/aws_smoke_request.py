#!/usr/bin/env python3
"""Run a real deployed ZeroLLM smoke request.

The script expects an already-deployed stack with model configs seeded and a
reachable StreamingApiUrl output. It creates a temporary API key, verifies
/v1/models, then retries a small chat completion until the cold start finishes
or the timeout expires.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path
from typing import Any

import boto3
import requests

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))


def stack_output(stack_name: str, region: str, key: str) -> str:
    cf = boto3.client("cloudformation", region_name=region)
    stack = cf.describe_stacks(StackName=stack_name)["Stacks"][0]
    for output in stack.get("Outputs", []):
        if output.get("OutputKey") == key:
            return output["OutputValue"]
    raise RuntimeError(f"Stack {stack_name!r} has no output {key!r}")


def create_api_key(email: str, name: str, environment: str, region: str) -> dict[str, Any]:
    from control_plane.backends.aws.state import DynamoDBStateStore
    from control_plane.core.keys import create_key

    state = DynamoDBStateStore(
        instances_table=f"zerollm-instances-{environment}",
        models_table=f"zerollm-models-{environment}",
        api_keys_table=f"zerollm-api-keys-{environment}",
        region_name=region,
    )
    return create_key(email=email, name=name, state=state)


def request_json(method: str, url: str, token: str, **kwargs: Any) -> requests.Response:
    headers = kwargs.pop("headers", {})
    headers["Authorization"] = f"Bearer {token}"
    headers.setdefault("Content-Type", "application/json")
    return requests.request(method, url, headers=headers, timeout=120, **kwargs)


def extract_text(payload: dict[str, Any]) -> str:
    choices = payload.get("choices") or []
    if choices:
        message = choices[0].get("message") or {}
        content = message.get("content")
        if isinstance(content, str):
            return content

    output = payload.get("output") or []
    texts: list[str] = []
    for item in output:
        for part in item.get("content") or []:
            if isinstance(part, dict) and part.get("text"):
                texts.append(str(part["text"]))
    return "\n".join(texts)


def main() -> None:
    parser = argparse.ArgumentParser(description="Run an AWS ZeroLLM smoke request")
    parser.add_argument("--stack-name", default=os.environ.get("STACK_NAME", "zerollm"))
    parser.add_argument("--environment", default=os.environ.get("ENVIRONMENT", "dev"))
    parser.add_argument("--region", default=os.environ.get("AWS_REGION"))
    parser.add_argument("--model", default=os.environ.get("SMOKE_MODEL", "Qwen/Qwen3.5-4B"))
    parser.add_argument("--prompt", default=os.environ.get("SMOKE_PROMPT", "Reply with exactly: zerollm smoke ok"))
    parser.add_argument("--email", default=os.environ.get("SMOKE_EMAIL", "ci@zerollm.local"))
    parser.add_argument("--timeout-seconds", type=int, default=int(os.environ.get("SMOKE_TIMEOUT_SECONDS", "2400")))
    parser.add_argument("--retry-seconds", type=int, default=int(os.environ.get("SMOKE_RETRY_SECONDS", "30")))
    args = parser.parse_args()

    if not args.region:
        raise SystemExit("AWS region is required via --region or AWS_REGION")

    base_url = stack_output(args.stack_name, args.region, "StreamingApiUrl").rstrip("/")
    key = create_api_key(args.email, "aws-smoke", args.environment, args.region)["key"]
    print(f"StreamingApiUrl={base_url}")
    print(f"Model={args.model}")

    models_resp = request_json("GET", f"{base_url}/v1/models", key)
    print(f"GET /v1/models -> {models_resp.status_code}")
    models_resp.raise_for_status()
    model_ids = [item["id"] for item in models_resp.json().get("data", [])]
    print(f"Models={model_ids}")
    if args.model not in model_ids:
        raise RuntimeError(f"Expected model {args.model!r} in /v1/models")

    deadline = time.time() + args.timeout_seconds
    attempt = 0
    last_body = ""
    while time.time() < deadline:
        attempt += 1
        resp = request_json(
            "POST",
            f"{base_url}/v1/chat/completions",
            key,
            json={
                "model": args.model,
                "messages": [{"role": "user", "content": args.prompt}],
                "max_tokens": 24,
                "temperature": 0,
            },
        )
        last_body = resp.text[:2000]
        print(f"POST /v1/chat/completions attempt={attempt} -> {resp.status_code}")
        if resp.status_code == 200:
            payload = resp.json()
            text = extract_text(payload)
            if not text.strip():
                raise RuntimeError(f"200 response did not contain assistant text: {payload}")
            print(f"Assistant={text.strip()!r}")
            return
        if resp.status_code != 503:
            raise RuntimeError(f"Unexpected status {resp.status_code}: {last_body}")
        retry_after = resp.headers.get("Retry-After")
        sleep_seconds = int(retry_after) if retry_after and retry_after.isdigit() else args.retry_seconds
        time.sleep(min(sleep_seconds, max(1, int(deadline - time.time()))))

    raise TimeoutError(f"Smoke request did not complete in {args.timeout_seconds}s. Last body: {last_body}")


if __name__ == "__main__":
    main()
