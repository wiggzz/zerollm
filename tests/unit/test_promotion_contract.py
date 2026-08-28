"""Behavioral contracts for the smoke-to-production promotion boundary."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
PROMOTION_METADATA = REPO_ROOT / "scripts" / "promotion_metadata.py"
INFRA_DEPLOY = REPO_ROOT / "infrastructure" / "deploy.sh"


def _run_metadata(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(PROMOTION_METADATA), *args],
        cwd=REPO_ROOT,
        check=False,
        capture_output=True,
        text=True,
    )


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


def test_infrastructure_deploy_accepts_prod_and_uses_the_prod_root(tmp_path: Path):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    log = tmp_path / "commands.log"

    (bin_dir / "aws").write_text(
        "#!/usr/bin/env bash\n"
        "set -euo pipefail\n"
        "test \"$1 $2\" = 's3api head-bucket'\n"
    )
    (bin_dir / "terraform").write_text(
        "#!/usr/bin/env bash\n"
        "set -euo pipefail\n"
        "printf '%s|%s\\n' \"$PWD\" \"$*\" >> \"$COMMAND_LOG\"\n"
    )
    for executable in bin_dir.iterdir():
        executable.chmod(0o755)

    result = subprocess.run(
        ["bash", str(INFRA_DEPLOY), "prod"],
        cwd=REPO_ROOT,
        env={
            **os.environ,
            "PATH": f"{bin_dir}:{os.environ['PATH']}",
            "COMMAND_LOG": str(log),
        },
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr
    assert log.read_text().splitlines() == [
        f"{REPO_ROOT}/infrastructure/environments/prod|init -migrate-state -force-copy",
        f"{REPO_ROOT}/infrastructure/environments/prod|apply",
    ]
