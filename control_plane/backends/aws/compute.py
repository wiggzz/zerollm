"""EC2 compute backend — launches and terminates GPU instances."""

from __future__ import annotations

import logging

import boto3
from botocore.exceptions import ClientError

logger = logging.getLogger(__name__)


class EC2ComputeBackend:
    def __init__(
        self,
        ami_id: str,
        security_group_id: str,
        subnet_id: str,
        instance_profile_arn: str,
        vllm_api_key: str = "",
        endpoint_url: str | None = None,
    ):
        kwargs = {}
        if endpoint_url:
            kwargs["endpoint_url"] = endpoint_url
        self._ec2 = boto3.client("ec2", **kwargs)
        self._ami_id = ami_id
        self._security_group_id = security_group_id
        # Accept a single subnet ID or a comma-separated list for multi-AZ fallback.
        self._subnet_ids = [s.strip() for s in subnet_id.split(",") if s.strip()]
        self._instance_profile_arn = instance_profile_arn
        self._vllm_api_key = vllm_api_key

    def launch(self, model_config: dict) -> tuple[str, str]:
        """Launch an EC2 GPU instance for the given model config.

        Tries each configured subnet in order, falling back on InsufficientInstanceCapacity.
        Returns (instance_id, public_ip).
        """
        user_data = self._build_user_data(model_config)
        last_exc = None

        for subnet_id in self._subnet_ids:
            try:
                resp = self._ec2.run_instances(
                    ImageId=self._ami_id,
                    InstanceType=model_config["instance_type"],
                    MinCount=1,
                    MaxCount=1,
                    SecurityGroupIds=[self._security_group_id],
                    SubnetId=subnet_id,
                    IamInstanceProfile={"Arn": self._instance_profile_arn},
                    UserData=user_data,
                    TagSpecifications=[
                        {
                            "ResourceType": "instance",
                            "Tags": [
                                {"Key": "Name", "Value": f"diogenes-{model_config['name']}"},
                                {"Key": "diogenes:model", "Value": model_config["name"]},
                            ],
                        }
                    ],
                )
                break
            except ClientError as exc:
                if exc.response["Error"]["Code"] == "InsufficientInstanceCapacity":
                    logger.warning(
                        "InsufficientInstanceCapacity in subnet %s, trying next", subnet_id
                    )
                    last_exc = exc
                    continue
                raise
        else:
            raise last_exc

        instance = resp["Instances"][0]
        instance_id = instance["InstanceId"]

        # Public IP is used since Lambda runs outside the VPC.
        # It may not be present in the run_instances response yet, so poll
        # describe_instances until it appears (usually within a few seconds).
        import time
        public_ip = instance.get("PublicIpAddress", "")
        if not public_ip:
            for _ in range(10):
                time.sleep(2)
                desc = self._ec2.describe_instances(InstanceIds=[instance_id])
                public_ip = desc["Reservations"][0]["Instances"][0].get("PublicIpAddress", "")
                if public_ip:
                    break

        return instance_id, public_ip

    def terminate(self, instance_id: str) -> None:
        self._ec2.terminate_instances(InstanceIds=[instance_id])

    def _build_user_data(self, model_config: dict) -> str:
        """Build the cloud-init script that starts vLLM."""
        vllm_args = model_config.get("vllm_args", "")
        if self._vllm_api_key:
            vllm_args = f"{vllm_args} --api-key {self._vllm_api_key}".strip()
        # model_id is the HuggingFace path for vLLM; falls back to name.
        model_id = model_config.get("model_id") or model_config["name"]
        return f"""#!/bin/bash
set -euo pipefail

# Write model config
cat > /etc/diogenes-model.env << 'MODELEOF'
MODEL_NAME={model_id}
VLLM_ARGS="{vllm_args}"
MODELEOF

# Start vLLM (assumes AMI has vllm installed and systemd service configured)
systemctl start vllm
"""
