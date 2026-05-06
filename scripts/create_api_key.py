#!/usr/bin/env python3
"""Create a ZeroLLM API key directly in DynamoDB."""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))


def main() -> None:
    parser = argparse.ArgumentParser(description="Create a ZeroLLM API key")
    parser.add_argument("--email", required=True, help="Owner email")
    parser.add_argument("--name", default="default", help="Human-friendly key name")
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
    args = parser.parse_args()

    env = args.environment

    from control_plane.backends.aws.state import DynamoDBStateStore
    from control_plane.core.keys import create_key

    state = DynamoDBStateStore(
        instances_table=f"zerollm-instances-{env}",
        models_table=f"zerollm-models-{env}",
        api_keys_table=f"zerollm-api-keys-{env}",
        region_name=args.region,
    )
    created = create_key(email=args.email, name=args.name, state=state)
    print(json.dumps(created, indent=2))


if __name__ == "__main__":
    main()
