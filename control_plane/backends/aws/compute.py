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
        models_bucket: str = "",
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
        self._models_bucket = models_bucket

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
                    # Model downloads and llama.cpp --no-mmap loads are mostly sequential.
                    # Throughput matters more than IOPS, and g5.xlarge/g5.2xlarge EBS
                    # bandwidth caps below 500 MiB/s, so 16k IOPS / 1000 MiB/s is wasted.
                    BlockDeviceMappings=[
                        {
                            "DeviceName": "/dev/sda1",
                            "Ebs": {
                                "VolumeType": "gp3",
                                "Throughput": 500,
                                "Iops": 3000,
                            },
                        }
                    ],
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

    def start(self, instance_id: str) -> str:
        """Start a stopped EC2 instance and return its public IP."""
        self._ec2.start_instances(InstanceIds=[instance_id])
        waiter = self._ec2.get_waiter("instance_running")
        waiter.wait(InstanceIds=[instance_id])
        return self._public_ip(instance_id)

    def stop(self, instance_id: str) -> None:
        self._ec2.stop_instances(InstanceIds=[instance_id])

    def instance_status(self, instance_id: str) -> dict:
        desc = self._ec2.describe_instances(InstanceIds=[instance_id])
        instance = desc["Reservations"][0]["Instances"][0]
        return {
            "state": instance["State"]["Name"],
            "ip": instance.get("PublicIpAddress", ""),
        }

    def _public_ip(self, instance_id: str) -> str:
        import time

        public_ip = ""
        for _ in range(10):
            desc = self._ec2.describe_instances(InstanceIds=[instance_id])
            public_ip = desc["Reservations"][0]["Instances"][0].get("PublicIpAddress", "")
            if public_ip:
                break
            time.sleep(2)
        return public_ip

    def _build_user_data(self, model_config: dict) -> str:
        """Build the cloud-init script that starts llama-server."""
        vllm_args = model_config.get("vllm_args", "")
        if self._vllm_api_key:
            vllm_args = f"{vllm_args} --api-key {self._vllm_api_key}".strip()
        model_id = model_config.get("model_id") or model_config["name"]
        model_name = model_config.get("name", model_id)
        model_name_safe = model_name.replace("/", "_")

        # If a models bucket and s3_key are present, download from S3 at boot.
        # Writing fresh bytes to EBS runs at full provisioned throughput (1000 MB/s),
        # bypassing the ~33 MB/s EBS snapshot lazy-initialization penalty.
        s3_key = model_config.get("s3_key", "")
        if self._models_bucket and s3_key:
            fetch_model = (
                f"log_step 'model_download_start bucket={self._models_bucket} key={s3_key}'\n"
                f"mkdir -p /opt/models\n"
                f"if test -s {model_id}; then\n"
                f"  log_step 'model_download_skip_existing path={model_id} size_bytes='$(stat -c%s {model_id})\n"
                f"else\n"
                f"  aws s3 cp s3://{self._models_bucket}/{s3_key} {model_id} --no-progress\n"
                f"  sync {model_id}\n"
                f"fi\n"
                f"log_step 'model_download_done path={model_id} size_bytes='$(stat -c%s {model_id})"
            )
        else:
            fetch_model = (
                f"log_step 'model_prebaked_expected path={model_id}'\n"
                f"test -s {model_id}\n"
                f"log_step 'model_prebaked_found path={model_id} size_bytes='$(stat -c%s {model_id})"
            )

        return f"""#!/bin/bash
set -euo pipefail

log_step() {{
  printf '%s %s\\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$*" | tee -a /var/log/diogenes-coldstart.log /var/log/vllm.log
}}

log_step 'cloud_init_start model={model_name}'

# Write model config
cat > /etc/diogenes-model.env << 'MODELEOF'
MODEL_NAME={model_id}
VLLM_ARGS="{vllm_args}"
MODELEOF
log_step 'model_env_written model_id={model_id}'

# Configure CloudWatch Logs agent to stream vLLM logs
INSTANCE_ID=$(curl -sf --connect-timeout 3 http://169.254.169.254/latest/meta-data/instance-id 2>/dev/null || echo "unknown")
if command -v /opt/aws/amazon-cloudwatch-agent/bin/amazon-cloudwatch-agent-ctl >/dev/null 2>&1; then
  cat > /opt/aws/amazon-cloudwatch-agent/etc/amazon-cloudwatch-agent.json << CWEOF
{{
  "agent": {{"run_as_user": "root"}},
  "logs": {{
    "logs_collected": {{
      "files": {{
        "collect_list": [
          {{
            "file_path": "/var/log/vllm.log",
            "log_group_name": "/diogenes/vllm",
            "log_stream_name": "$INSTANCE_ID/{model_name_safe}",
            "timezone": "UTC",
            "retention_in_days": 7
          }},
          {{
            "file_path": "/var/log/diogenes-coldstart.log",
            "log_group_name": "/diogenes/coldstart",
            "log_stream_name": "$INSTANCE_ID/{model_name_safe}",
            "timezone": "UTC",
            "retention_in_days": 7
          }}
        ]
      }}
    }}
  }}
}}
CWEOF
  /opt/aws/amazon-cloudwatch-agent/bin/amazon-cloudwatch-agent-ctl \
    -a fetch-config -m ec2 -s \
    -c file:/opt/aws/amazon-cloudwatch-agent/etc/amazon-cloudwatch-agent.json || true
fi
log_step 'cloudwatch_agent_configured'

{fetch_model}

# Start vLLM (assumes AMI has llama-server configured via systemd)
log_step 'llama_service_start'
systemctl enable vllm
systemctl start vllm
log_step 'cloud_init_done'
"""
