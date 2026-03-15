#!/usr/bin/env python3
"""Seed default model configurations into the Diogenes DynamoDB models table."""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

DEFAULT_MODELS = [
    {
        "name": "Qwen/Qwen3.5-27B",
        "instance_type": "g6.12xlarge",
        "vllm_args": "--tensor-parallel-size 4 --max-model-len 32768 --reasoning-parser qwen3 --enable-auto-tool-choice --tool-call-parser qwen3_coder",
        "idle_timeout": 300,
    },
    {
        "name": "Qwen/Qwen3.5-4B",
        "instance_type": "g5.xlarge",
        "vllm_args": "--max-model-len 32768 --reasoning-parser qwen3 --enable-auto-tool-choice --tool-call-parser qwen3_coder",
        "idle_timeout": 300,
    },
]


def main() -> None:
    parser = argparse.ArgumentParser(description="Seed Diogenes model configurations into DynamoDB")
    parser.add_argument(
        "--environment",
        default=os.environ.get("ENVIRONMENT", "dev"),
        help="Stack environment suffix (default: dev)",
    )
    parser.add_argument(
        "--region",
        default=os.environ.get("AWS_REGION"),
        help="AWS region (or set AWS_REGION)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print models that would be seeded without writing to DynamoDB",
    )
    args = parser.parse_args()

    table_name = f"diogenes-models-{args.environment}"

    if args.dry_run:
        print(f"Would seed {len(DEFAULT_MODELS)} model(s) into {table_name}:")
        for model in DEFAULT_MODELS:
            print(json.dumps(model, indent=2))
        return

    import boto3
    dynamodb = boto3.resource("dynamodb", region_name=args.region)
    table = dynamodb.Table(table_name)

    for model in DEFAULT_MODELS:
        table.put_item(Item=model)
        print(f"Seeded: {model['name']} ({model['instance_type']})")

    print(f"\nDone — {len(DEFAULT_MODELS)} model(s) written to {table_name}")


if __name__ == "__main__":
    main()
