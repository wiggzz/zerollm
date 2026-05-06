#!/usr/bin/env bash
set -euo pipefail

# Managed AMI build flow using AWS Image Builder.
#
# Usage:
#   ./ami/imagebuilder.sh deploy
#   ./ami/imagebuilder.sh start
#   ./ami/imagebuilder.sh latest
#   ./ami/imagebuilder.sh build   # deploy + start + wait + print AMI
#
# Required env vars:
#   AWS_REGION
#
# Optional env vars:
#   AMI_PIPELINE_STACK     (default: zerollm-ami-pipeline)
#   AMI_PIPELINE_ENV       (default: dev)
#   BASE_AMI_ID            (auto-selected by region if omitted)
#   BUILDER_SUBNET_ID      (auto-selected if omitted)
#   BUILDER_SECURITY_GROUP_ID (auto-selected if omitted)
#   BUILDER_INSTANCE_TYPE  (default: t3.small)
#   IMAGE_VERSION          (default: 1.0.2)
#   PIPELINE_STATUS        (default: DISABLED)

require() {
  local name="$1"
  if [[ -z "${!name:-}" ]]; then
    echo "Missing required env var: ${name}" >&2
    exit 1
  fi
}

require AWS_REGION

if ! command -v aws >/dev/null 2>&1; then
  echo "aws CLI not found on PATH" >&2
  exit 1
fi

cmd="${1:-build}"
AMI_PIPELINE_STACK="${AMI_PIPELINE_STACK:-zerollm-ami-pipeline}"
AMI_PIPELINE_ENV="${AMI_PIPELINE_ENV:-dev}"
BUILDER_INSTANCE_TYPE="${BUILDER_INSTANCE_TYPE:-c5.2xlarge}"
PIPELINE_STATUS="${PIPELINE_STATUS:-DISABLED}"
TEMPLATE_FILE="${TEMPLATE_FILE:-ami/imagebuilder-template.yaml}"

# Read these from the template file so they stay in sync automatically.
# Can still be overridden via env vars.
_template_default() {
  local param="$1"
  awk "/^  ${param}:/{f=1} f && /Default:/{sub(/^[[:space:]]*Default:[[:space:]]*/,\"\"); print; exit}" "${TEMPLATE_FILE}"
}
IMAGE_VERSION="${IMAGE_VERSION:-$(_template_default ImageVersion)}"
PRIMARY_MODEL_GGUF_REPO="${PRIMARY_MODEL_GGUF_REPO:-$(_template_default PrimaryModelGgufRepo)}"
PRIMARY_MODEL_GGUF_FILE="${PRIMARY_MODEL_GGUF_FILE:-$(_template_default PrimaryModelGgufFile)}"
SMALL_MODEL_GGUF_REPO="${SMALL_MODEL_GGUF_REPO:-$(_template_default SmallModelGgufRepo)}"
SMALL_MODEL_GGUF_FILE="${SMALL_MODEL_GGUF_FILE:-$(_template_default SmallModelGgufFile)}"

default_base_ami_for_region() {
  case "$1" in
    ap-southeast-2) echo "ami-021000ae4658b3c28" ;;
    us-east-2)      echo "ami-0600d0aaccc95db72" ;;
    us-west-2)      echo "ami-0a08f4510bfe41148" ;;
    *) echo "" ;;
  esac
}

resolve_subnet_id() {
  local subnet_id
  subnet_id="$(
    aws ec2 describe-subnets \
      --region "${AWS_REGION}" \
      --filters "Name=default-for-az,Values=true" "Name=state,Values=available" \
      --query "sort_by(Subnets,&AvailabilityZone)[0].SubnetId" \
      --output text 2>/dev/null || true
  )"
  if [[ -n "${subnet_id}" && "${subnet_id}" != "None" ]]; then
    echo "${subnet_id}"
    return 0
  fi
  subnet_id="$(
    aws ec2 describe-subnets \
      --region "${AWS_REGION}" \
      --filters "Name=state,Values=available" \
      --query "sort_by(Subnets,&AvailabilityZone)[0].SubnetId" \
      --output text 2>/dev/null || true
  )"
  if [[ -n "${subnet_id}" && "${subnet_id}" != "None" ]]; then
    echo "${subnet_id}"
    return 0
  fi
  return 1
}

resolve_vpc_for_subnet() {
  aws ec2 describe-subnets \
    --region "${AWS_REGION}" \
    --subnet-ids "${BUILDER_SUBNET_ID}" \
    --query "Subnets[0].VpcId" \
    --output text
}

resolve_security_group_id() {
  local sg_id
  sg_id="$(
    aws ec2 describe-security-groups \
      --region "${AWS_REGION}" \
      --filters "Name=vpc-id,Values=${vpc_id}" "Name=group-name,Values=default" \
      --query "SecurityGroups[0].GroupId" \
      --output text 2>/dev/null || true
  )"
  if [[ -n "${sg_id}" && "${sg_id}" != "None" ]]; then
    echo "${sg_id}"
    return 0
  fi
  sg_id="$(
    aws ec2 describe-security-groups \
      --region "${AWS_REGION}" \
      --filters "Name=vpc-id,Values=${vpc_id}" \
      --query "SecurityGroups[0].GroupId" \
      --output text 2>/dev/null || true
  )"
  if [[ -n "${sg_id}" && "${sg_id}" != "None" ]]; then
    echo "${sg_id}"
    return 0
  fi
  return 1
}

resolve_defaults() {
  if [[ -z "${BASE_AMI_ID:-}" ]]; then
    BASE_AMI_ID="$(default_base_ami_for_region "${AWS_REGION}")"
  fi
  if [[ -z "${BASE_AMI_ID:-}" ]]; then
    echo "Missing BASE_AMI_ID and no regional default exists for AWS_REGION=${AWS_REGION}" >&2
    exit 1
  fi

  if [[ -z "${BUILDER_SUBNET_ID:-}" ]]; then
    if BUILDER_SUBNET_ID="$(resolve_subnet_id)"; then
      echo "Auto-selected BUILDER_SUBNET_ID=${BUILDER_SUBNET_ID}"
    else
      echo "Missing BUILDER_SUBNET_ID and no subnet could be auto-discovered in ${AWS_REGION}" >&2
      exit 1
    fi
  fi

  vpc_id="$(resolve_vpc_for_subnet)"
  if [[ -z "${vpc_id}" || "${vpc_id}" == "None" ]]; then
    echo "Unable to determine VPC for subnet ${BUILDER_SUBNET_ID}" >&2
    exit 1
  fi

  if [[ -z "${BUILDER_SECURITY_GROUP_ID:-}" ]]; then
    if BUILDER_SECURITY_GROUP_ID="$(resolve_security_group_id)"; then
      echo "Auto-selected BUILDER_SECURITY_GROUP_ID=${BUILDER_SECURITY_GROUP_ID} (vpc=${vpc_id})"
    else
      echo "Missing BUILDER_SECURITY_GROUP_ID and no security group could be auto-discovered for VPC ${vpc_id}" >&2
      exit 1
    fi
  fi
}

deploy_pipeline_stack() {
  resolve_defaults
  echo "Deploying Image Builder stack ${AMI_PIPELINE_STACK} in ${AWS_REGION}..."
  local output rc
  set +e
  output="$(
    aws cloudformation deploy \
      --region "${AWS_REGION}" \
      --stack-name "${AMI_PIPELINE_STACK}" \
      --template-file "${TEMPLATE_FILE}" \
      --capabilities CAPABILITY_NAMED_IAM \
      --parameter-overrides \
        Environment="${AMI_PIPELINE_ENV}" \
        BaseAmiId="${BASE_AMI_ID}" \
        BuilderSubnetId="${BUILDER_SUBNET_ID}" \
        BuilderSecurityGroupId="${BUILDER_SECURITY_GROUP_ID}" \
        BuilderInstanceType="${BUILDER_INSTANCE_TYPE}" \
        ImageVersion="${IMAGE_VERSION}" \
        PipelineStatus="${PIPELINE_STATUS}" \
        PrimaryModelGgufRepo="${PRIMARY_MODEL_GGUF_REPO}" \
        PrimaryModelGgufFile="${PRIMARY_MODEL_GGUF_FILE}" \
        SmallModelGgufRepo="${SMALL_MODEL_GGUF_REPO}" \
        SmallModelGgufFile="${SMALL_MODEL_GGUF_FILE}" 2>&1
  )"
  rc=$?
  set -e

  if [[ ${rc} -ne 0 ]]; then
    if echo "${output}" | grep -q "No changes to deploy"; then
      echo "${output}"
      echo "Stack is up to date; continuing."
    else
      echo "${output}" >&2
      return "${rc}"
    fi
  else
    echo "${output}"
  fi
}

get_pipeline_arn() {
  aws cloudformation describe-stacks \
    --region "${AWS_REGION}" \
    --stack-name "${AMI_PIPELINE_STACK}" \
    --query "Stacks[0].Outputs[?OutputKey=='ImagePipelineArn'].OutputValue | [0]" \
    --output text 2>/dev/null || echo ""
}

wait_for_build() {
  local image_build_arn="$1"
  echo "Waiting for image build: ${image_build_arn}"

  while true; do
    local status reason
    status="$(
      aws imagebuilder get-image \
        --region "${AWS_REGION}" \
        --image-build-version-arn "${image_build_arn}" \
        --query "image.state.status" \
        --output text
    )"
    reason="$(
      aws imagebuilder get-image \
        --region "${AWS_REGION}" \
        --image-build-version-arn "${image_build_arn}" \
        --query "image.state.reason" \
        --output text 2>/dev/null || true
    )"
    case "${status}" in
      AVAILABLE)
        local ami_id
        ami_id="$(
          aws imagebuilder get-image \
            --region "${AWS_REGION}" \
            --image-build-version-arn "${image_build_arn}" \
            --query "image.outputResources.amis[0].image" \
            --output text
        )"
        if [[ -z "${ami_id}" || "${ami_id}" == "None" ]]; then
          ami_id="$(
            aws imagebuilder get-image \
              --region "${AWS_REGION}" \
              --image-build-version-arn "${image_build_arn}" \
              --query "image.outputResources.amis[0].imageId" \
              --output text
          )"
        fi
        echo "AMI is ready: ${ami_id}"
        echo "Use this value for GpuAmiId."
        break
        ;;
      FAILED | CANCELLED)
        echo "Image build failed with status=${status}" >&2
        aws imagebuilder get-image \
          --region "${AWS_REGION}" \
          --image-build-version-arn "${image_build_arn}" \
          --query "image.state" \
          --output json >&2 || true
        if [[ -n "${reason}" && "${reason}" != "None" ]]; then
          local workflow_execution_id
          workflow_execution_id="$(echo "${reason}" | sed -n "s/.*Workflow Execution ID: '\\([^']*\\)'.*/\\1/p")"
          if [[ -n "${workflow_execution_id}" ]]; then
            echo "Workflow execution diagnostics (${workflow_execution_id}):" >&2
            aws imagebuilder get-workflow-execution \
              --region "${AWS_REGION}" \
              --workflow-execution-id "${workflow_execution_id}" \
              --output json >&2 || true
            aws imagebuilder list-workflow-step-executions \
              --region "${AWS_REGION}" \
              --workflow-execution-id "${workflow_execution_id}" \
              --output json >&2 || true

            local failed_steps
            failed_steps="$(
              aws imagebuilder list-workflow-step-executions \
                --region "${AWS_REGION}" \
                --workflow-execution-id "${workflow_execution_id}" \
                --query "steps[?status=='FAILED'].stepExecutionId" \
                --output text 2>/dev/null || true
            )"
            if [[ -n "${failed_steps}" && "${failed_steps}" != "None" ]]; then
              local step_id
              for step_id in ${failed_steps}; do
                echo "Failed step details (${step_id}):" >&2
                local step_json step_outputs run_command_id
                step_json="$(
                  aws imagebuilder get-workflow-step-execution \
                    --region "${AWS_REGION}" \
                    --step-execution-id "${step_id}" \
                    --output json
                )"
                echo "${step_json}" >&2

                step_outputs="$(
                  aws imagebuilder get-workflow-step-execution \
                    --region "${AWS_REGION}" \
                    --step-execution-id "${step_id}" \
                    --query "outputs" \
                    --output text 2>/dev/null || true
                )"
                run_command_id="$(echo "${step_outputs}" | sed -n 's/.*"runCommandId": "\([^"]*\)".*/\1/p')"
                if [[ -n "${run_command_id}" ]]; then
                  echo "SSM command diagnostics (${run_command_id}):" >&2
                  aws ssm list-command-invocations \
                    --region "${AWS_REGION}" \
                    --command-id "${run_command_id}" \
                    --details \
                    --output json >&2 || true
                fi
              done
            fi
          fi
        fi
        exit 1
        ;;
      *)
        echo "Current status: ${status}. Waiting 30s..."
        sleep 30
        ;;
    esac
  done
}

start_pipeline_build() {
  local pipeline_arn
  pipeline_arn="$(get_pipeline_arn)"
  if [[ -z "${pipeline_arn}" || "${pipeline_arn}" == "None" ]]; then
    echo "ImagePipelineArn not found. Run deploy first." >&2
    exit 1
  fi

  local image_build_arn
  image_build_arn="$(
    aws imagebuilder start-image-pipeline-execution \
      --region "${AWS_REGION}" \
      --image-pipeline-arn "${pipeline_arn}" \
      --query "imageBuildVersionArn" \
      --output text
  )"
  echo "Started image build: ${image_build_arn}"
  wait_for_build "${image_build_arn}"
}

print_latest_ami() {
  local pipeline_arn
  pipeline_arn="$(get_pipeline_arn)"
  if [[ -z "${pipeline_arn}" || "${pipeline_arn}" == "None" ]]; then
    echo "ImagePipelineArn not found. Run deploy first." >&2
    exit 1
  fi

  local latest_image_arn
  latest_image_arn="$(
    aws imagebuilder list-image-pipeline-images \
      --region "${AWS_REGION}" \
      --image-pipeline-arn "${pipeline_arn}" \
      --query "sort_by(imageSummaryList[?state.status=='AVAILABLE'],&dateCreated)[-1].arn" \
      --output text
  )"
  if [[ -z "${latest_image_arn}" || "${latest_image_arn}" == "None" ]]; then
    echo "No images found for pipeline ${pipeline_arn}" >&2
    exit 1
  fi

  local ami_id
  ami_id="$(
    aws imagebuilder get-image \
      --region "${AWS_REGION}" \
      --image-build-version-arn "${latest_image_arn}" \
      --query "image.outputResources.amis[0].image" \
      --output text
  )"
  if [[ -z "${ami_id}" || "${ami_id}" == "None" ]]; then
    ami_id="$(
      aws imagebuilder get-image \
        --region "${AWS_REGION}" \
        --image-build-version-arn "${latest_image_arn}" \
        --query "image.outputResources.amis[0].imageId" \
        --output text
    )"
  fi
  echo "${ami_id}"
}

prune_old_amis() {
  local keep="${KEEP:-2}"
  local pipeline_arn
  pipeline_arn="$(get_pipeline_arn)"
  if [[ -z "${pipeline_arn}" || "${pipeline_arn}" == "None" ]]; then
    echo "ImagePipelineArn not found. Run deploy first." >&2
    exit 1
  fi

  # All available images sorted oldest-first; skip the newest $keep, delete the rest
  local all_arns
  mapfile -t all_arns < <(
    aws imagebuilder list-image-pipeline-images \
      --region "${AWS_REGION}" \
      --image-pipeline-arn "${pipeline_arn}" \
      --query "sort_by(imageSummaryList[?state.status=='AVAILABLE'],&dateCreated)[*].arn" \
      --output text | tr '\t' '\n'
  )

  local total="${#all_arns[@]}"
  if [[ "${total}" -le "${keep}" ]]; then
    echo "Only ${total} AMI(s) found, nothing to prune (keeping ${keep})."
    return 0
  fi

  local to_delete=$(( total - keep ))
  echo "Found ${total} AMIs. Keeping latest ${keep}, deleting ${to_delete}."

  for i in $(seq 0 $(( to_delete - 1 ))); do
    local image_arn="${all_arns[$i]}"
    local ami_id
    ami_id="$(aws imagebuilder get-image --region "${AWS_REGION}" \
      --image-build-version-arn "${image_arn}" \
      --query "image.outputResources.amis[0].image" --output text 2>/dev/null || true)"
    if [[ -z "${ami_id}" || "${ami_id}" == "None" ]]; then
      ami_id="$(aws imagebuilder get-image --region "${AWS_REGION}" \
        --image-build-version-arn "${image_arn}" \
        --query "image.outputResources.amis[0].imageId" --output text 2>/dev/null || true)"
    fi

    echo "Deleting image ${image_arn} (AMI: ${ami_id})..."

    # Deregister the AMI and delete its snapshots
    if [[ -n "${ami_id}" && "${ami_id}" != "None" ]]; then
      local snapshot_ids
      mapfile -t snapshot_ids < <(
        aws ec2 describe-images --region "${AWS_REGION}" --image-ids "${ami_id}" \
          --query "Images[0].BlockDeviceMappings[*].Ebs.SnapshotId" \
          --output text | tr '\t' '\n' | grep -v "None" || true
      )
      aws ec2 deregister-image --region "${AWS_REGION}" --image-id "${ami_id}" && \
        echo "  Deregistered AMI ${ami_id}"
      for snap in "${snapshot_ids[@]}"; do
        [[ -z "${snap}" ]] && continue
        aws ec2 delete-snapshot --region "${AWS_REGION}" --snapshot-id "${snap}" && \
          echo "  Deleted snapshot ${snap}"
      done
    fi
  done
  echo "Prune complete."
}

case "${cmd}" in
  deploy)
    deploy_pipeline_stack
    ;;
  start)
    start_pipeline_build
    ;;
  latest)
    print_latest_ami
    ;;
  build)
    deploy_pipeline_stack
    start_pipeline_build
    ;;
  prune)
    prune_old_amis
    ;;
  *)
    echo "Unknown command: ${cmd}" >&2
    echo "Usage: $0 [deploy|start|latest|build|prune]" >&2
    exit 1
    ;;
esac
