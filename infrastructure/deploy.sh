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

cd "$(dirname "$0")/environments/${environment}"

terraform init
terraform apply
