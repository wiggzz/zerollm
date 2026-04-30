"""Unit tests for AWS EC2 compute user data generation."""

from __future__ import annotations

from control_plane.backends.aws.compute import EC2ComputeBackend


def _backend(models_bucket: str = "diogenes-models-dev-123") -> EC2ComputeBackend:
    backend = EC2ComputeBackend.__new__(EC2ComputeBackend)
    backend._vllm_api_key = "secret"
    backend._models_bucket = models_bucket
    return backend


def test_build_user_data_downloads_s3_model_and_logs_cold_start_steps():
    user_data = _backend()._build_user_data(
        {
            "name": "Qwen/Qwen3.5-27B",
            "model_id": "/opt/models/model.gguf",
            "s3_key": "model.gguf",
            "vllm_args": "-ngl 99 --ctx-size 32768 --parallel 1 --jinja",
        }
    )

    assert "aws s3 cp s3://diogenes-models-dev-123/model.gguf /opt/models/model.gguf" in user_data
    assert "if test -s /opt/models/model.gguf" in user_data
    assert "log_step 'model_download_skip_existing path=/opt/models/model.gguf size_bytes='" in user_data
    assert "log_step 'model_download_start bucket=diogenes-models-dev-123 key=model.gguf'" in user_data
    assert "log_step 'model_download_done path=/opt/models/model.gguf size_bytes='" in user_data
    assert 'log_group_name": "/diogenes/coldstart"' in user_data
    assert "--api-key secret" in user_data
    assert "systemctl enable vllm" in user_data


def test_build_user_data_validates_prebaked_model_when_s3_key_absent():
    user_data = _backend()._build_user_data(
        {
            "name": "Qwen/Qwen3.5-4B",
            "model_id": "/opt/models/small.gguf",
            "vllm_args": "-ngl 99 --ctx-size 131072 --parallel 1 --jinja",
        }
    )

    assert "aws s3 cp" not in user_data
    assert "log_step 'model_prebaked_expected path=/opt/models/small.gguf'" in user_data
    assert "test -s /opt/models/small.gguf" in user_data
