"""Unit tests for AWS EC2 compute user data generation."""

from __future__ import annotations

from control_plane.backends.aws.compute import EC2ComputeBackend


def _backend(models_bucket: str = "zerollm-models-dev-123") -> EC2ComputeBackend:
    backend = EC2ComputeBackend.__new__(EC2ComputeBackend)
    backend._vllm_api_key = "secret"
    backend._models_bucket = models_bucket
    return backend


def test_build_user_data_writes_env_and_enables_service():
    """Cloud-init writes env file and enables service; model fetch is in start_vllm.sh."""
    user_data = _backend()._build_user_data(
        {
            "name": "Qwen/Qwen3.5-27B",
            "model_id": "/opt/models/model.gguf",
            "s3_key": "model.gguf",
            "vllm_args": "-ngl 99 --ctx-size 32768 --parallel 1 --jinja",
        }
    )

    # Cloud-init writes env with S3 creds for start_vllm.sh to use
    assert "S3_BUCKET=zerollm-models-dev-123" in user_data
    assert "S3_KEY=model.gguf" in user_data
    # Cloud-init does NOT download model anymore (unified in start_vllm.sh)
    assert "aws s3 cp" not in user_data
    # Cloud-init enables vllm service (start_vllm.sh handles model fetch)
    assert "systemctl enable vllm" in user_data
    assert "systemctl start vllm" not in user_data  # systemd auto-starts on boot
    assert 'log_group_name": "/zerollm/coldstart"' in user_data
    assert "--api-key secret" in user_data


def test_launch_tags_instance_and_volume_with_stack_ownership():
    class FakeEC2:
        def run_instances(self, **kwargs):
            self.kwargs = kwargs
            return {"Instances": [{"InstanceId": "i-123", "PublicIpAddress": "203.0.113.10"}]}

    ec2 = FakeEC2()
    backend = EC2ComputeBackend.__new__(EC2ComputeBackend)
    backend._ec2 = ec2
    backend._ami_id = "ami-123"
    backend._security_group_id = "sg-123"
    backend._subnet_ids = ["subnet-123"]
    backend._instance_profile_arn = "arn:aws:iam::123:instance-profile/zerollm"
    backend._vllm_api_key = ""
    backend._models_bucket = ""
    backend._instance_tags = {
        "Environment": "ci123",
        "zerollm:stack-id": "arn:aws:cloudformation:us-east-2:123:stack/zerollm-smoke/abc",
    }

    instance_id, public_ip = backend.launch(
        {
            "name": "Qwen/Qwen3.5-4B",
            "instance_type": "g5.xlarge",
            "model_id": "/opt/models/small.gguf",
        }
    )

    assert (instance_id, public_ip) == ("i-123", "203.0.113.10")
    tag_specs = ec2.kwargs["TagSpecifications"]
    assert {spec["ResourceType"] for spec in tag_specs} == {"instance", "volume"}
    for spec in tag_specs:
        tags = {tag["Key"]: tag["Value"] for tag in spec["Tags"]}
        assert tags["zerollm:model"] == "Qwen/Qwen3.5-4B"
        assert tags["Project"] == "zerollm"
        assert tags["Environment"] == "ci123"
        assert tags["zerollm:stack-id"] == "arn:aws:cloudformation:us-east-2:123:stack/zerollm-smoke/abc"
