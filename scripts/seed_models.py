#!/usr/bin/env python3
"""Seed default model configurations into the Diogenes DynamoDB models table.

Usage:
  python3 scripts/seed_models.py                          # seed DynamoDB only
  python3 scripts/seed_models.py --upload                 # download from HF, upload to stack bucket, seed DynamoDB
  python3 scripts/seed_models.py --dry-run                # print what would be seeded
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

# Each model has:
#   name         — canonical model name (used as DynamoDB key and in API requests)
#   hf_repo      — HuggingFace repo to download the GGUF from
#   hf_file      — filename within that repo
#   model_id     — absolute path where the file will live on the GPU instance
#   instance_type, vllm_args, idle_timeout — runtime config
DEFAULT_MODELS = [
    {
        "name": "Qwen/Qwen3.5-27B",
        "hf_repo": "bartowski/Qwen_Qwen3.5-27B-GGUF",
        "hf_file": "Qwen_Qwen3.5-27B-Q4_K_M.gguf",
        "model_id": "/opt/models/Qwen_Qwen3.5-27B-Q4_K_M.gguf",
        "instance_type": "g5.2xlarge",
        # llama-server flags: full GPU offload, 32k context, one slot to reduce cold-start
        # memory pressure on a single A10G.
        # --no-mmap forces sequential EBS reads vs page-fault random I/O.
        "vllm_args": "-ngl 99 --ctx-size 32768 --parallel 1 --jinja",
        "idle_timeout": 300,
    },
    {
        "name": "Qwen/Qwen3.5-4B",
        "hf_repo": "bartowski/Qwen_Qwen3.5-4B-GGUF",
        "hf_file": "Qwen_Qwen3.5-4B-Q4_K_M.gguf",
        "model_id": "/opt/models/Qwen_Qwen3.5-4B-Q4_K_M.gguf",
        "instance_type": "g5.xlarge",
        # llama-server flags: full GPU offload, 128k context, single slot (parallel 1 so full
        # 22GB VRAM is available for KV cache; n_parallel=4 default would OOM at 128k).
        "vllm_args": "-ngl 99 --ctx-size 131072 --parallel 1 --jinja",
        "idle_timeout": 300,
    },
]

_VLLM_ONLY_FLAGS = {
    "--max-model-len",
    "--reasoning-parser",
    "--enable-auto-tool-choice",
    "--tool-call-parser",
    "--enforce-eager",
    "--tensor-parallel-size",
    "--dtype",
    "--quantization",
}


def validate_model(model: dict) -> None:
    """Raise ValueError if a model config looks wrong for llama-server."""
    name = model.get("name", "?")

    model_id = model.get("model_id") or model.get("name", "")
    if not model_id.startswith("/"):
        raise ValueError(
            f"Model '{name}': model_id must be an absolute path to a GGUF file "
            f"(e.g. /opt/models/foo.gguf), got: {model_id!r}"
        )
    if not model_id.endswith(".gguf"):
        raise ValueError(
            f"Model '{name}': model_id must point to a .gguf file, got: {model_id!r}"
        )

    server_args = model.get("vllm_args", "")
    bad = [flag for flag in _VLLM_ONLY_FLAGS if flag in server_args]
    if bad:
        raise ValueError(
            f"Model '{name}': vllm_args contains vLLM-only flag(s) not supported by "
            f"llama-server: {bad}. Use llama-server flags like -ngl, --ctx-size, --jinja."
        )


def upload_model(model: dict, bucket: str, region: str | None) -> None:
    """Download model from HuggingFace and upload to S3 if not already present."""
    import boto3
    from huggingface_hub import hf_hub_download

    hf_repo = model["hf_repo"]
    hf_file = model["hf_file"]
    name = model["name"]

    s3 = boto3.client("s3", region_name=region)

    # Check if already uploaded — skip if so.
    try:
        s3.head_object(Bucket=bucket, Key=hf_file)
        print(f"  {name}: s3://{bucket}/{hf_file} already exists, skipping upload")
        return
    except s3.exceptions.ClientError:
        pass
    except Exception:
        pass

    print(f"  {name}: downloading {hf_repo}/{hf_file} from HuggingFace...")
    local_path = hf_hub_download(repo_id=hf_repo, filename=hf_file)
    size_gb = Path(local_path).stat().st_size / 1024 ** 3
    print(f"  {name}: uploading {size_gb:.1f} GB to s3://{bucket}/{hf_file}...")
    s3.upload_file(local_path, bucket, hf_file)
    print(f"  {name}: upload complete")


def discover_models_bucket(stack_name: str, region: str | None) -> str:
    """Return the deployed stack's ModelsBucketName output."""
    import boto3

    cf = boto3.client("cloudformation", region_name=region)
    resp = cf.describe_stacks(StackName=stack_name)
    outputs = resp["Stacks"][0].get("Outputs", [])
    for output in outputs:
        if output.get("OutputKey") == "ModelsBucketName":
            return output["OutputValue"]
    raise RuntimeError(
        f"Stack {stack_name!r} does not expose ModelsBucketName. Deploy the latest template first."
    )


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
        "--upload",
        action="store_true",
        help="Download models from HuggingFace and upload to S3 before seeding DynamoDB",
    )
    parser.add_argument(
        "--use-s3",
        action="store_true",
        help="Write s3_key into model configs without uploading files",
    )
    parser.add_argument(
        "--bucket",
        default=os.environ.get("MODELS_BUCKET"),
        help="S3 bucket for model files (defaults to stack ModelsBucketName when needed)",
    )
    parser.add_argument(
        "--stack-name",
        default=os.environ.get("STACK_NAME", "diogenes"),
        help="CloudFormation stack name used to discover the model bucket (default: diogenes)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print models that would be seeded without writing to DynamoDB or S3",
    )
    args = parser.parse_args()

    uses_s3 = args.upload or args.use_s3
    bucket = args.bucket or None
    if uses_s3 and not bucket:
        bucket = discover_models_bucket(args.stack_name, args.region)

    table_name = f"diogenes-models-{args.environment}"

    for model in DEFAULT_MODELS:
        validate_model(model)

    if args.dry_run:
        print(f"Would seed {len(DEFAULT_MODELS)} model(s) into {table_name}:")
        for model in DEFAULT_MODELS:
            item = {k: v for k, v in model.items() if k not in ("hf_repo", "hf_file")}
            if bucket:
                item["s3_key"] = model["hf_file"]
            print(json.dumps(item, indent=2))
        return

    if args.upload:
        print(f"Uploading {len(DEFAULT_MODELS)} model(s) to s3://{bucket}/...")
        for model in DEFAULT_MODELS:
            upload_model(model, bucket, args.region)
        print()

    import boto3
    dynamodb = boto3.resource("dynamodb", region_name=args.region)
    table = dynamodb.Table(table_name)

    for model in DEFAULT_MODELS:
        item = {k: v for k, v in model.items() if k not in ("hf_repo", "hf_file")}
        if bucket:
            item["s3_key"] = model["hf_file"]
        table.put_item(Item=item)
        print(f"Seeded: {model['name']} ({model['instance_type']})")

    print(f"\nDone — {len(DEFAULT_MODELS)} model(s) written to {table_name}")


if __name__ == "__main__":
    main()
