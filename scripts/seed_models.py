#!/usr/bin/env python3
"""Seed default model configurations into the ZeroLLM DynamoDB models table.

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

DEFAULT_MANIFEST = REPO_ROOT / "models.json"
DEFAULT_HF_RANGE_GETS = "8"
DEFAULT_S3_CHUNK_MB = 64
PROGRESS_STEP_BYTES = 512 * 1024 * 1024

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


def configure_hf_transfer_environment() -> None:
    """Use bounded Hugging Face Xet defaults while allowing caller overrides."""
    os.environ.setdefault("HF_XET_HIGH_PERFORMANCE", "1")
    os.environ.setdefault("HF_XET_NUM_CONCURRENT_RANGE_GETS", DEFAULT_HF_RANGE_GETS)


def load_models(manifest_path: str | Path = DEFAULT_MANIFEST) -> list[dict]:
    """Load model definitions from the repo manifest."""
    path = Path(manifest_path)
    with path.open() as f:
        manifest = json.load(f)
    models = manifest.get("models")
    if not isinstance(models, list) or not models:
        raise ValueError(f"{path}: expected a non-empty 'models' list")
    return models


def s3_key_for(model: dict) -> str:
    return model.get("s3_key") or model["hf_file"]


def seed_item_for(model: dict, bucket: str | None) -> dict:
    item = {
        k: v
        for k, v in model.items()
        if k not in ("hf_repo", "hf_file", "hf_revision")
    }
    if bucket:
        item["s3_key"] = s3_key_for(model)
    return item


def prune_stale_models(table, model_names: set[str]) -> list[str]:
    """Delete model rows that are no longer present in the manifest."""
    deleted = []
    resp = table.scan(ProjectionExpression="#name", ExpressionAttributeNames={"#name": "name"})
    while True:
        for item in resp.get("Items", []):
            name = item["name"]
            if name not in model_names:
                table.delete_item(Key={"name": name})
                deleted.append(name)
        if "LastEvaluatedKey" not in resp:
            break
        resp = table.scan(
            ProjectionExpression="#name",
            ExpressionAttributeNames={"#name": "name"},
            ExclusiveStartKey=resp["LastEvaluatedKey"],
        )
    return deleted


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


def _int_from_env(name: str, default: int) -> int:
    raw = os.environ.get(name)
    if not raw:
        return default
    try:
        value = int(raw)
    except ValueError as exc:
        raise ValueError(f"{name} must be an integer, got {raw!r}") from exc
    if value <= 0:
        raise ValueError(f"{name} must be positive, got {value}")
    return value


class UploadProgress:
    """Print coarse progress for long model uploads."""

    def __init__(self, name: str, step_bytes: int = PROGRESS_STEP_BYTES):
        self._name = name
        self._step_bytes = step_bytes
        self._seen = 0
        self._next_report = step_bytes

    def __call__(self, bytes_amount: int) -> None:
        self._seen += bytes_amount
        if self._seen < self._next_report:
            return

        gib = self._seen / 1024**3
        print(f"  {self._name}: streamed {gib:.1f} GiB to S3", flush=True)
        while self._next_report <= self._seen:
            self._next_report += self._step_bytes


def upload_model(model: dict, bucket: str, region: str | None) -> None:
    """Stream a model from Hugging Face to S3 if not already present."""
    import boto3
    from huggingface_hub import HfFileSystem

    hf_repo = model["hf_repo"]
    hf_file = model["hf_file"]
    hf_revision = model.get("hf_revision")
    name = model["name"]
    s3_key = s3_key_for(model)

    s3 = boto3.client("s3", region_name=region)

    # Check if already uploaded — skip if so.
    try:
        s3.head_object(Bucket=bucket, Key=s3_key)
        print(f"  {name}: s3://{bucket}/{s3_key} already exists, skipping upload")
        return
    except s3.exceptions.ClientError:
        pass
    except Exception:
        pass

    chunk_mb = _int_from_env("S3_MULTIPART_CHUNK_MB", DEFAULT_S3_CHUNK_MB)
    chunk_bytes = chunk_mb * 1024 * 1024
    hf_path = f"{hf_repo}/{hf_file}"
    token = os.environ.get("HF_TOKEN") or None
    fs = HfFileSystem(token=token, block_size=0)

    print(
        f"  {name}: streaming hf://{hf_path} to s3://{bucket}/{s3_key} "
        f"(chunk={chunk_mb}MiB)...",
        flush=True,
    )
    with fs.open(hf_path, "rb", revision=hf_revision, block_size=0) as remote_file:
        upload_stream_to_s3(
            s3=s3,
            fileobj=remote_file,
            bucket=bucket,
            key=s3_key,
            chunk_bytes=chunk_bytes,
            progress=UploadProgress(name),
        )
    print(f"  {name}: upload complete")


def upload_stream_to_s3(
    *,
    s3,
    fileobj,
    bucket: str,
    key: str,
    chunk_bytes: int,
    progress: UploadProgress,
) -> None:
    """Upload a non-seekable stream to S3 with sequential multipart upload."""
    upload_id = None
    parts = []
    try:
        created = s3.create_multipart_upload(Bucket=bucket, Key=key)
        upload_id = created["UploadId"]

        part_number = 1
        while True:
            chunk = fileobj.read(chunk_bytes)
            if not chunk:
                break

            uploaded = s3.upload_part(
                Bucket=bucket,
                Key=key,
                UploadId=upload_id,
                PartNumber=part_number,
                Body=chunk,
            )
            parts.append({"PartNumber": part_number, "ETag": uploaded["ETag"]})
            progress(len(chunk))
            part_number += 1

        if not parts:
            raise RuntimeError(f"No bytes read while uploading s3://{bucket}/{key}")

        s3.complete_multipart_upload(
            Bucket=bucket,
            Key=key,
            UploadId=upload_id,
            MultipartUpload={"Parts": parts},
        )
        upload_id = None
    finally:
        if upload_id is not None:
            s3.abort_multipart_upload(Bucket=bucket, Key=key, UploadId=upload_id)


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
    parser = argparse.ArgumentParser(description="Seed ZeroLLM model configurations into DynamoDB")
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
        "--manifest",
        default=os.environ.get("MODELS_MANIFEST", str(DEFAULT_MANIFEST)),
        help="Model manifest path (default: models.json)",
    )
    parser.add_argument(
        "--table-name",
        default=os.environ.get("MODELS_TABLE"),
        help="DynamoDB models table name (default: zerollm-models-<environment>)",
    )
    parser.add_argument(
        "--stack-name",
        default=os.environ.get("STACK_NAME", "zerollm"),
        help="CloudFormation stack name used to discover the model bucket (default: zerollm)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print models that would be seeded without writing to DynamoDB or S3",
    )
    parser.add_argument(
        "--no-prune",
        action="store_true",
        help="Do not delete model rows that are absent from the manifest",
    )
    args = parser.parse_args()

    uses_s3 = args.upload or args.use_s3
    bucket = args.bucket or None
    if uses_s3 and not bucket:
        bucket = discover_models_bucket(args.stack_name, args.region)

    table_name = args.table_name or f"zerollm-models-{args.environment}"
    models = load_models(args.manifest)

    for model in models:
        validate_model(model)

    if args.dry_run:
        print(f"Would seed {len(models)} model(s) into {table_name}:")
        for model in models:
            item = seed_item_for(model, bucket)
            print(json.dumps(item, indent=2))
        if not args.no_prune:
            print("Would prune model rows not present in manifest")
        return

    if args.upload:
        configure_hf_transfer_environment()
        print(f"Uploading {len(models)} model(s) to s3://{bucket}/...")
        for model in models:
            upload_model(model, bucket, args.region)
        print()

    import boto3
    dynamodb = boto3.resource("dynamodb", region_name=args.region)
    table = dynamodb.Table(table_name)

    for model in models:
        item = seed_item_for(model, bucket)
        table.put_item(Item=item)
        print(f"Seeded: {model['name']} ({model['instance_type']})")

    if not args.no_prune:
        deleted = prune_stale_models(table, {model["name"] for model in models})
        for name in deleted:
            print(f"Pruned: {name}")

    print(f"\nDone — {len(models)} model(s) written to {table_name}")


if __name__ == "__main__":
    main()
