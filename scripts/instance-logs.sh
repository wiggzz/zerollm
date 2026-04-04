#!/usr/bin/env bash
# Show vLLM startup logs and cluster status for active GPU instances.
#
# Usage:
#   ./scripts/instance-logs.sh                    # logs for all active instances
#   MODEL_FILTER=Qwen3.5-4B ./scripts/instance-logs.sh   # filter by model
#   LINES=100 ./scripts/instance-logs.sh          # more lines (default: 60)
set -euo pipefail

AWS_REGION="${AWS_REGION:-$(aws configure get region 2>/dev/null || true)}"
ENVIRONMENT="${ENVIRONMENT:-dev}"
INSTANCES_TABLE="diogenes-instances-${ENVIRONMENT}"
LINES="${LINES:-60}"
MODEL_FILTER="${MODEL_FILTER:-}"

if [[ -z "${AWS_REGION}" ]]; then
  echo "AWS_REGION is required" >&2
  exit 1
fi

# Fetch all non-terminated instances into a temp file
tmpfile="$(mktemp)"
trap 'rm -f "$tmpfile"' EXIT

aws dynamodb scan \
  --region "${AWS_REGION}" \
  --table-name "${INSTANCES_TABLE}" \
  --filter-expression "#s <> :t" \
  --expression-attribute-names '{"#s":"status"}' \
  --expression-attribute-values '{":t":{"S":"terminated"}}' \
  --output json > "$tmpfile" 2>&1 || { echo "DynamoDB scan failed: $(cat "$tmpfile")" >&2; exit 1; }

count="$(python3 -c "import json; data=json.load(open('$tmpfile')); print(len(data.get('Items',[])))")"
if [[ "${count}" == "0" ]]; then
  echo "No active instances found in ${INSTANCES_TABLE}."
  exit 0
fi

python3 <<PYEOF
import json, subprocess, os, time

data = json.load(open("$tmpfile"))
region = "$AWS_REGION"
lines = "$LINES"
model_filter = "$MODEL_FILTER"

def g(item, key):
    return list(item.get(key, {}).values())[0] if key in item else ""

for item in data.get("Items", []):
    ec2_id = g(item, "provider_instance_id")
    model  = g(item, "model")
    status = g(item, "status")
    ip     = g(item, "ip")

    if model_filter and model_filter not in model:
        continue

    launched = g(item, "launched_at")
    if launched:
        age_s = int(time.time()) - int(launched)
        age   = f"{age_s // 60}m{age_s % 60}s"
    else:
        age = "?"

    print(f"\n{'=' * 70}")
    print(f"  model   : {model}")
    print(f"  status  : {status}  (age: {age})")
    print(f"  ip      : {ip}")
    print(f"  ec2     : {ec2_id}")
    print(f"{'=' * 70}")

    if not ec2_id or not ec2_id.startswith("i-"):
        print("  (no EC2 instance ID recorded)")
        continue

    # Check EC2 state
    try:
        r = subprocess.run(
            ["aws", "ec2", "describe-instances", "--region", region,
             "--instance-ids", ec2_id,
             "--query", "Reservations[0].Instances[0].State.Name",
             "--output", "text"],
            capture_output=True, text=True, timeout=10
        )
        print(f"  ec2 state: {r.stdout.strip()}")
    except Exception as e:
        print(f"  ec2 state: error ({e})")

    # Check health endpoint
    try:
        r = subprocess.run(
            ["curl", "-sf", "--connect-timeout", "3", "--max-time", "5",
             f"http://{ip}:8000/health"],
            capture_output=True, text=True, timeout=8
        )
        print(f"  health  : {'UP' if r.returncode == 0 else 'DOWN'}")
    except Exception:
        print(f"  health  : DOWN")

    print(f"\n--- vLLM journal (last {lines} lines via SSM) ---")

    # Send SSM command
    try:
        cmd_result = subprocess.run(
            ["aws", "ssm", "send-command",
             "--region", region,
             "--instance-ids", ec2_id,
             "--document-name", "AWS-RunShellScript",
             "--parameters",
             f"commands=['journalctl -u vllm -n {lines} --no-pager 2>&1']",
             "--output", "json"],
            capture_output=True, text=True, timeout=15
        )
        if cmd_result.returncode != 0:
            print(f"SSM send-command failed: {cmd_result.stderr[:300]}")
            continue

        cmd_data = json.loads(cmd_result.stdout)
        cmd_id = cmd_data["Command"]["CommandId"]

        # Poll for result (up to 30s)
        for attempt in range(10):
            time.sleep(3)
            out = subprocess.run(
                ["aws", "ssm", "get-command-invocation",
                 "--region", region,
                 "--command-id", cmd_id,
                 "--instance-id", ec2_id,
                 "--output", "json"],
                capture_output=True, text=True, timeout=10
            )
            if out.returncode != 0:
                continue
            inv = json.loads(out.stdout)
            inv_status = inv.get("Status", "")
            if inv_status in ("InProgress", "Pending", "Delayed"):
                print(f"  (waiting for SSM... {inv_status})", end="\r", flush=True)
                continue
            content = inv.get("StandardOutputContent", "").strip()
            err_content = inv.get("StandardErrorContent", "").strip()
            print(content or "(no output)")
            if err_content:
                print(f"[stderr] {err_content[:500]}")
            break
        else:
            print("  (SSM command timed out)")

    except Exception as e:
        print(f"  SSM error: {e}")

print()
PYEOF
