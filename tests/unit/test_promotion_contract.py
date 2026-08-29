"""Behavioral contracts for the smoke-to-production promotion boundary."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

import yaml


REPO_ROOT = Path(__file__).resolve().parents[2]
PROMOTION_METADATA = REPO_ROOT / "scripts" / "promotion_metadata.py"
PRODUCTION_WORKFLOW = REPO_ROOT / ".github" / "workflows" / "production-deploy.yml"


def _run_metadata(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(PROMOTION_METADATA), *args],
        cwd=REPO_ROOT,
        check=False,
        capture_output=True,
        text=True,
    )


def _load_workflow(path: Path) -> dict[str, Any]:
    document = yaml.load(path.read_text(), Loader=yaml.BaseLoader)
    assert isinstance(document, dict)
    return document


def test_production_workflow_uses_existing_dev_trust_without_an_enable_toggle():
    workflow = _load_workflow(PRODUCTION_WORKFLOW)
    deploy = workflow["jobs"]["deploy"]

    assert deploy["environment"] == "dev"
    assert "PRODUCTION_DEPLOY_ENABLED" not in deploy["if"]
    assert "zerollm-deployment-role-dev" in deploy["env"]["AWS_ROLE_TO_ASSUME"]
    assert "zerollm-cloudformation-execution-role-dev" in deploy["env"]["CFN_ROLE_ARN"]


def test_promotion_metadata_round_trip_binds_ami_manifest_and_smoke_sha(tmp_path: Path):
    manifest = tmp_path / "models.json"
    manifest.write_text('{"models":["Qwen/Qwen3.8-27B"]}\n')
    metadata = tmp_path / "promotion.json"
    sha = "a" * 40

    written = _run_metadata(
        "write",
        "--output",
        str(metadata),
        "--commit-sha",
        sha,
        "--region",
        "us-east-2",
        "--ami-id",
        "ami-0123456789abcdef0",
        "--manifest",
        str(manifest),
    )

    assert written.returncode == 0, written.stderr
    assert json.loads(metadata.read_text())["commit_sha"] == sha

    validated = _run_metadata(
        "validate",
        "--metadata",
        str(metadata),
        "--expected-commit-sha",
        sha,
        "--manifest",
        str(manifest),
    )

    assert validated.returncode == 0, validated.stderr
    resolved = dict(line.split("=", 1) for line in validated.stdout.splitlines())
    assert resolved == {
        "AWS_REGION": "us-east-2",
        "GPU_AMI_ID": "ami-0123456789abcdef0",
    }

    consumer = subprocess.run(
        [
            "bash",
            "-u",
            "-c",
            'test "$AWS_REGION" = us-east-2 && test "$GPU_AMI_ID" = ami-0123456789abcdef0',
        ],
        env={**os.environ, **resolved},
        check=False,
        capture_output=True,
        text=True,
    )
    assert consumer.returncode == 0, consumer.stderr


def test_promotion_metadata_rejects_an_unsafe_region_value(tmp_path: Path):
    manifest = tmp_path / "models.json"
    manifest.write_text('{"models":["Qwen/Qwen3.8-27B"]}\n')

    written = _run_metadata(
        "write",
        "--output",
        str(tmp_path / "promotion.json"),
        "--commit-sha",
        "c" * 40,
        "--region",
        "us-east-2\nGPU_AMI_ID=ami-0123456789abcdef0",
        "--ami-id",
        "ami-0123456789abcdef0",
        "--manifest",
        str(manifest),
    )

    assert written.returncode != 0
    assert "region" in written.stderr


def test_promotion_metadata_rejects_a_manifest_that_drifted_after_smoke(tmp_path: Path):
    manifest = tmp_path / "models.json"
    manifest.write_text('{"models":["Qwen/Qwen3.8-27B"]}\n')
    metadata = tmp_path / "promotion.json"
    sha = "b" * 40
    assert _run_metadata(
        "write",
        "--output",
        str(metadata),
        "--commit-sha",
        sha,
        "--region",
        "us-east-2",
        "--ami-id",
        "ami-0123456789abcdef0",
        "--manifest",
        str(manifest),
    ).returncode == 0

    manifest.write_text('{"models":["Qwen/Qwen3.8-27B","tampered"]}\n')
    validated = _run_metadata(
        "validate",
        "--metadata",
        str(metadata),
        "--expected-commit-sha",
        sha,
        "--manifest",
        str(manifest),
    )

    assert validated.returncode != 0
    assert "manifest SHA-256" in validated.stderr
