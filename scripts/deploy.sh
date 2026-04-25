#!/usr/bin/env bash
set -euo pipefail

# One-command deploy for Diogenes.
# - Sync requirements + sam build
# - Resolve/build GPU AMI
# - Auto-discover subnet/security group defaults
# - Run non-guided sam deploy with parameter overrides
#
# Required:
#   AWS credentials configured
#   AWS region configured via AWS_REGION or `aws configure get region`
#
# Optional env vars:
#   STACK_NAME (default: diogenes)
#   ENVIRONMENT (default: dev)
#   AWS_REGION (or uses aws config default region)
#   GPU_AMI_ID (skip AMI lookup/build if set)
#   GPU_SUBNET_ID (auto-selected if unset)
#   GPU_SECURITY_GROUP_ID (auto-selected if unset)
#   DEPLOY_DEFAULTS_FILE (default: .diogenes/deploy-<region>-<stack>.env)
#   ALLOWED_EMAILS
#   GOOGLE_CLIENT_ID
#   AMI_BUILD_MODE (auto|latest|build, default: auto)
#   HF_TOKEN_SECRET_ARN (optional Secrets Manager secret ARN used by CodeBuild model sync)
#   SYNC_MODELS_ON_DEPLOY (1|0, default: 1)
#   AMI_PIPELINE_STACK, AMI_PIPELINE_ENV, BASE_AMI_ID, BUILDER_SUBNET_ID,
#   BUILDER_SECURITY_GROUP_ID, BUILDER_INSTANCE_TYPE, IMAGE_VERSION, PIPELINE_STATUS

require_cmd() {
  if ! command -v "$1" >/dev/null 2>&1; then
    echo "Missing required command: $1" >&2
    exit 1
  fi
}

require_cmd aws
require_cmd sam
require_cmd uv

AWS_REGION="${AWS_REGION:-$(aws configure get region 2>/dev/null || true)}"
STACK_NAME="${STACK_NAME:-diogenes}"
ENVIRONMENT="${ENVIRONMENT:-dev}"
AMI_BUILD_MODE="${AMI_BUILD_MODE:-auto}"
DEPLOY_DEFAULTS_FILE="${DEPLOY_DEFAULTS_FILE:-.diogenes/deploy-${AWS_REGION}-${STACK_NAME}.env}"

if [[ -z "${AWS_REGION}" ]]; then
  echo "AWS region is not set. Set AWS_REGION or configure a default AWS region." >&2
  exit 1
fi

echo "Using AWS_REGION=${AWS_REGION}"

resolve_subnet_id() {
  # Return all default-VPC subnets across AZs (comma-separated) so the compute
  # backend can fall back to another AZ on InsufficientInstanceCapacity.
  local subnet_ids
  subnet_ids="$(
    aws ec2 describe-subnets \
      --region "${AWS_REGION}" \
      --filters "Name=default-for-az,Values=true" "Name=state,Values=available" \
      --query "join(',', sort_by(Subnets,&AvailabilityZone)[*].SubnetId)" \
      --output text 2>/dev/null || true
  )"
  if [[ -n "${subnet_ids}" && "${subnet_ids}" != "None" ]]; then
    echo "${subnet_ids}"
    return 0
  fi
  subnet_ids="$(
    aws ec2 describe-subnets \
      --region "${AWS_REGION}" \
      --filters "Name=state,Values=available" \
      --query "join(',', sort_by(Subnets,&AvailabilityZone)[*].SubnetId)" \
      --output text 2>/dev/null || true
  )"
  if [[ -n "${subnet_ids}" && "${subnet_ids}" != "None" ]]; then
    echo "${subnet_ids}"
    return 0
  fi
  return 1
}

resolve_vpc_for_subnet() {
  # Use only the first subnet ID when looking up the VPC.
  local first_subnet="${GPU_SUBNET_ID%%,*}"
  aws ec2 describe-subnets \
    --region "${AWS_REGION}" \
    --subnet-ids "${first_subnet}" \
    --query "Subnets[0].VpcId" \
    --output text
}

load_pinned_defaults() {
  if [[ -f "${DEPLOY_DEFAULTS_FILE}" ]]; then
    # shellcheck disable=SC1090
    source "${DEPLOY_DEFAULTS_FILE}"
    echo "Loaded pinned deploy defaults from ${DEPLOY_DEFAULTS_FILE}"
  fi
}

save_pinned_defaults() {
  mkdir -p "$(dirname "${DEPLOY_DEFAULTS_FILE}")"
  cat > "${DEPLOY_DEFAULTS_FILE}" <<EOF
GPU_SUBNET_ID=${GPU_SUBNET_ID}
VLLM_API_KEY=${VLLM_API_KEY}
EOF
  echo "Saved pinned deploy defaults to ${DEPLOY_DEFAULTS_FILE}"
}

stack_output() {
  local output_key="$1"
  aws cloudformation describe-stacks \
    --region "${AWS_REGION}" \
    --stack-name "${STACK_NAME}" \
    --query "Stacks[0].Outputs[?OutputKey=='${output_key}'].OutputValue | [0]" \
    --output text
}

start_model_sync() {
  local bucket project hash prefix manifest_uri script_uri build_id

  bucket="$(stack_output ModelsBucketName)"
  project="$(stack_output ModelSyncProjectName)"
  if [[ -z "${bucket}" || "${bucket}" == "None" || -z "${project}" || "${project}" == "None" ]]; then
    echo "Model sync outputs not found; skipping model sync trigger"
    return 0
  fi

  hash="$(shasum -a 256 models.json scripts/seed_models.py | shasum -a 256 | awk '{print $1}')"
  prefix="model-sync/${hash}"
  manifest_uri="s3://${bucket}/${prefix}/models.json"
  script_uri="s3://${bucket}/${prefix}/seed_models.py"

  aws s3 cp models.json "${manifest_uri}" --region "${AWS_REGION}" >/dev/null
  aws s3 cp scripts/seed_models.py "${script_uri}" --region "${AWS_REGION}" >/dev/null

  build_id="$(
    aws codebuild start-build \
      --region "${AWS_REGION}" \
      --project-name "${project}" \
      --environment-variables-override \
        "name=MODEL_MANIFEST_S3_URI,value=${manifest_uri},type=PLAINTEXT" \
        "name=MODEL_SYNC_SCRIPT_S3_URI,value=${script_uri},type=PLAINTEXT" \
      --query "build.id" \
      --output text
  )"
  echo "Started model sync build: ${build_id}"
}

latest_pipeline_ami() {
  AWS_REGION="${AWS_REGION}" \
  AMI_PIPELINE_STACK="${AMI_PIPELINE_STACK:-diogenes-ami-pipeline}" \
  AMI_PIPELINE_ENV="${AMI_PIPELINE_ENV:-dev}" \
  ./ami/imagebuilder.sh latest
}

build_pipeline_ami() {
  AWS_REGION="${AWS_REGION}" \
  AMI_PIPELINE_STACK="${AMI_PIPELINE_STACK:-diogenes-ami-pipeline}" \
  AMI_PIPELINE_ENV="${AMI_PIPELINE_ENV:-dev}" \
  BASE_AMI_ID="${BASE_AMI_ID:-}" \
  BUILDER_SUBNET_ID="${BUILDER_SUBNET_ID:-}" \
  BUILDER_SECURITY_GROUP_ID="${BUILDER_SECURITY_GROUP_ID:-}" \
  PIPELINE_STATUS="${PIPELINE_STATUS:-DISABLED}" \
  ./ami/imagebuilder.sh build
}

if [[ -z "${GPU_AMI_ID:-}" ]]; then
  case "${AMI_BUILD_MODE}" in
    latest)
      GPU_AMI_ID="$(latest_pipeline_ami)"
      ;;
    build)
      build_pipeline_ami
      GPU_AMI_ID="$(latest_pipeline_ami)"
      ;;
    auto)
      if ! GPU_AMI_ID="$(latest_pipeline_ami 2>/dev/null)" || [[ -z "${GPU_AMI_ID}" || "${GPU_AMI_ID}" == "None" ]]; then
        echo "No AMI found in pipeline. Building a fresh AMI..."
        build_pipeline_ami
        GPU_AMI_ID="$(latest_pipeline_ami)"
      fi
      ;;
    *)
      echo "Invalid AMI_BUILD_MODE=${AMI_BUILD_MODE}. Use auto|latest|build." >&2
      exit 1
      ;;
  esac
fi

# Load pinned network defaults before attempting auto-discovery.
load_pinned_defaults

if [[ -z "${GPU_SUBNET_ID:-}" ]]; then
  if GPU_SUBNET_ID="$(resolve_subnet_id)"; then
    echo "Auto-selected GPU_SUBNET_ID=${GPU_SUBNET_ID}"
  else
    echo "Unable to auto-discover GPU_SUBNET_ID in ${AWS_REGION}" >&2
    exit 1
  fi
fi

vpc_id="$(resolve_vpc_for_subnet)"
if [[ -z "${vpc_id}" || "${vpc_id}" == "None" ]]; then
  echo "Unable to determine VPC for subnet ${GPU_SUBNET_ID}" >&2
  exit 1
fi

if [[ -z "${VLLM_API_KEY:-}" ]]; then
  VLLM_API_KEY="$(openssl rand -hex 32)"
  echo "Generated new VLLM_API_KEY"
fi

save_pinned_defaults

echo "Using STACK_NAME=${STACK_NAME}"
echo "Using GpuAmiId=${GPU_AMI_ID}"
echo "Using GpuSubnetId=${GPU_SUBNET_ID}"
echo "Using GpuVpcId=${vpc_id}"

uv export --project control_plane --no-dev --no-hashes --no-header --output-file requirements.txt
sam build

param_overrides=(
  "Environment=${ENVIRONMENT}"
  "GpuAmiId=${GPU_AMI_ID}"
  "GpuSubnetId=${GPU_SUBNET_ID}"
  "GpuVpcId=${vpc_id}"
)

if [[ -n "${ALLOWED_EMAILS:-}" ]]; then
  param_overrides+=("AllowedEmails=${ALLOWED_EMAILS}")
fi
if [[ -n "${GOOGLE_CLIENT_ID:-}" ]]; then
  param_overrides+=("GoogleClientId=${GOOGLE_CLIENT_ID}")
fi
if [[ -n "${VLLM_API_KEY:-}" ]]; then
  param_overrides+=("VllmApiKey=${VLLM_API_KEY}")
fi
if [[ -n "${HF_TOKEN_SECRET_ARN:-}" ]]; then
  param_overrides+=("HFTokenSecretArn=${HF_TOKEN_SECRET_ARN}")
fi

sam deploy \
  --region "${AWS_REGION}" \
  --stack-name "${STACK_NAME}" \
  --capabilities CAPABILITY_IAM CAPABILITY_NAMED_IAM \
  --resolve-s3 \
  --parameter-overrides "${param_overrides[@]}"

if [[ "${SYNC_MODELS_ON_DEPLOY:-1}" == "1" ]]; then
  start_model_sync
fi
