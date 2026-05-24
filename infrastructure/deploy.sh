#!/usr/bin/env bash
set -euo pipefail

environment="${1:-dev}"
case "${environment}" in
  dev) ;;
  *)
    echo "Usage: $0 dev" >&2
    exit 1
    ;;
esac

state_region="us-east-2"
state_bucket="zerollm-terraform-state-265978616089-us-east-2"

ensure_state_bucket() {
  if aws s3api head-bucket --bucket "${state_bucket}" >/dev/null 2>&1; then
    echo "Terraform state bucket exists: s3://${state_bucket}"
    return 0
  fi

  echo "Creating Terraform state bucket: s3://${state_bucket}"
  if [[ "${state_region}" == "us-east-1" ]]; then
    aws s3api create-bucket --bucket "${state_bucket}" --region "${state_region}"
  else
    aws s3api create-bucket \
      --bucket "${state_bucket}" \
      --region "${state_region}" \
      --create-bucket-configuration "LocationConstraint=${state_region}"
  fi

  aws s3api put-public-access-block \
    --bucket "${state_bucket}" \
    --public-access-block-configuration \
      BlockPublicAcls=true,IgnorePublicAcls=true,BlockPublicPolicy=true,RestrictPublicBuckets=true

  aws s3api put-bucket-versioning \
    --bucket "${state_bucket}" \
    --versioning-configuration Status=Enabled

  aws s3api put-bucket-encryption \
    --bucket "${state_bucket}" \
    --server-side-encryption-configuration \
      '{"Rules":[{"ApplyServerSideEncryptionByDefault":{"SSEAlgorithm":"AES256"}}]}'
}

ensure_state_bucket

cd "$(dirname "$0")/environments/${environment}"

terraform init -migrate-state -force-copy
terraform apply
