#!/usr/bin/env python3
"""Bind a successful smoke run to the immutable inputs promoted to production."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
from pathlib import Path
from typing import Any


COMMIT_SHA_RE = re.compile(r"^[0-9a-f]{40}$")
AMI_ID_RE = re.compile(r"^ami-[0-9a-f]{8,17}$")
AWS_REGION_RE = re.compile(r"^[a-z]{2}(?:-[a-z]+)+-\d+$")
REQUIRED_KEYS = {"schema_version", "commit_sha", "region", "ami_id", "manifest_sha256"}


class MetadataError(ValueError):
    """Promotion metadata is malformed, stale, or does not match the checkout."""


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def validate_commit_sha(value: str) -> str:
    if not COMMIT_SHA_RE.fullmatch(value):
        raise MetadataError("commit SHA must be a lowercase 40-character hexadecimal SHA")
    return value


def validate_ami_id(value: str) -> str:
    if not AMI_ID_RE.fullmatch(value):
        raise MetadataError("AMI ID must match ami-<8-17 lowercase hex characters>")
    return value


def validate_region(value: str) -> str:
    if not AWS_REGION_RE.fullmatch(value):
        raise MetadataError("AWS region must be a canonical region identifier")
    return value


def write_metadata(output: Path, commit_sha: str, region: str, ami_id: str, manifest: Path) -> None:
    if not manifest.is_file():
        raise MetadataError(f"manifest does not exist: {manifest}")

    payload = {
        "schema_version": 1,
        "commit_sha": validate_commit_sha(commit_sha),
        "region": validate_region(region),
        "ami_id": validate_ami_id(ami_id),
        "manifest_sha256": sha256_file(manifest),
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.with_name(f".{output.name}.tmp")
    temporary.write_text(json.dumps(payload, sort_keys=True) + "\n")
    os.replace(temporary, output)


def load_metadata(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text())
    except FileNotFoundError as error:
        raise MetadataError(f"promotion metadata does not exist: {path}") from error
    except json.JSONDecodeError as error:
        raise MetadataError(f"promotion metadata is not valid JSON: {error}") from error
    if not isinstance(value, dict):
        raise MetadataError("promotion metadata must be a JSON object")
    if set(value) != REQUIRED_KEYS:
        raise MetadataError(f"promotion metadata keys must be exactly {sorted(REQUIRED_KEYS)}")
    if value["schema_version"] != 1:
        raise MetadataError("unsupported promotion metadata schema version")
    if not isinstance(value["region"], str):
        raise MetadataError("promotion metadata region must be a string")
    validate_region(value["region"])
    if not isinstance(value["manifest_sha256"], str) or not re.fullmatch(r"[0-9a-f]{64}", value["manifest_sha256"]):
        raise MetadataError("promotion metadata manifest SHA-256 must be lowercase hexadecimal")
    validate_commit_sha(value["commit_sha"])
    validate_ami_id(value["ami_id"])
    return value


def validate_metadata(metadata_path: Path, expected_commit_sha: str, manifest: Path) -> dict[str, str]:
    if not manifest.is_file():
        raise MetadataError(f"manifest does not exist: {manifest}")
    metadata = load_metadata(metadata_path)
    expected_commit_sha = validate_commit_sha(expected_commit_sha)
    if metadata["commit_sha"] != expected_commit_sha:
        raise MetadataError("promotion metadata commit SHA does not match the successful smoke commit")
    if metadata["manifest_sha256"] != sha256_file(manifest):
        raise MetadataError("promotion metadata manifest SHA-256 does not match the checked-out manifest")
    return {"AWS_REGION": metadata["region"], "GPU_AMI_ID": metadata["ami_id"]}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)

    write = commands.add_parser("write", help="write immutable smoke-promotion metadata")
    write.add_argument("--output", type=Path, required=True)
    write.add_argument("--commit-sha", required=True)
    write.add_argument("--region", required=True)
    write.add_argument("--ami-id", required=True)
    write.add_argument("--manifest", type=Path, required=True)

    validate = commands.add_parser("validate", help="validate smoke-promotion metadata")
    validate.add_argument("--metadata", type=Path, required=True)
    validate.add_argument("--expected-commit-sha", required=True)
    validate.add_argument("--manifest", type=Path, required=True)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        if args.command == "write":
            write_metadata(args.output, args.commit_sha, args.region, args.ami_id, args.manifest)
            return 0
        values = validate_metadata(args.metadata, args.expected_commit_sha, args.manifest)
    except MetadataError as error:
        print(f"promotion metadata error: {error}", file=sys.stderr)
        return 1

    for key, value in values.items():
        print(f"{key}={value}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
