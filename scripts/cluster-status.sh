#!/usr/bin/env bash
# Print a quick table of active GPU instances and their states.
set -euo pipefail

AWS_REGION="${AWS_REGION:-$(aws configure get region 2>/dev/null || true)}"
ENVIRONMENT="${ENVIRONMENT:-dev}"
INSTANCES_TABLE="diogenes-instances-${ENVIRONMENT}"

if [[ -z "${AWS_REGION}" ]]; then
  echo "AWS_REGION is required" >&2
  exit 1
fi

scan_output="$(aws dynamodb scan \
  --region "${AWS_REGION}" \
  --table-name "${INSTANCES_TABLE}" \
  --output json 2>&1)" || { echo "DynamoDB scan failed: ${scan_output}" >&2; exit 1; }

echo "${scan_output}" | python3 -c "
import json, sys, time

data = json.load(sys.stdin)
items = data.get('Items', [])

if not items:
    print('No instances in table.')
    sys.exit(0)

def g(item, key):
    return list(item.get(key, {}).values())[0] if key in item else ''

print(f\"{'MODEL':<28} {'STATUS':<12} {'EC2 ID':<22} {'IP':<18} {'AGE'}\")
print('-' * 90)
for i in items:
    launched = g(i, 'launched_at')
    age = ''
    if launched:
        s = int(time.time()) - int(launched)
        age = f'{s // 60}m{s % 60}s'
    print(f\"{g(i,'model'):<28} {g(i,'status'):<12} {g(i,'provider_instance_id'):<22} {g(i,'ip'):<18} {age}\")
"
