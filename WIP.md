# WIP — Cold Start Follow-Up

## Current Repo State

- Worktree was clean after commit `a633b62 Improve cold-start model loading path`.
- Recent relevant commits:
  - `a633b62 Improve cold-start model loading path`
  - `d9819df Document current repo state`
  - `3ebaadd Load model weights from S3 at boot`
  - `1d70ab7 Add validate_model to seed_models.py to catch incompatible configs early`

## Deployed Stack

- Region: `us-east-2`
- Stack: `diogenes`
- Stack was updated successfully after `a633b62` changes.
- API URL: `https://c64j0mm1c4.execute-api.us-east-2.amazonaws.com`
- Model bucket output: `diogenes-models-dev-265978616089`
- Current deployed AMI parameter: `ami-0750beefa394b06e9`
- AMI pipeline stack is still on recipe `1.3.8`; repo AMI template is `1.3.9`.

## Cold Start Findings

- The stack had S3 model-bucket wiring, but the bucket was empty and DynamoDB model rows did not have `s3_key`, so instances still used pre-baked AMI model files.
- The 27B instance boot itself was not the main cost:
  - EC2 launch to system boot done: about 55 seconds.
  - `vllm.service` started about 67 seconds after EC2 launch.
- The main delay was `llama-server` reading/loading GGUF from snapshot-backed AMI storage:
  - Instance was still `/health = 503 Loading model` after 10+ minutes.
  - Process was in disk sleep and had read only about 12.7 GB.
  - Volume was gp3 `16000 IOPS / 1000 MiB/s`, but snapshot lazy initialization still dominated.
- Fast Snapshot Restore was not enabled for the AMI snapshot.

## Changes Implemented

- `scripts/seed_models.py`
  - Can discover `ModelsBucketName` from CloudFormation.
  - `--upload` downloads from Hugging Face and uploads to S3, then seeds DynamoDB with `s3_key`.
  - `--use-s3` writes `s3_key` without upload.
  - 27B default args reduced to `-ngl 99 --ctx-size 32768 --parallel 1 --jinja`.
- `pyproject.toml` / `uv.lock`
  - Added optional `upload` extra with `huggingface-hub`.
- `Makefile`
  - Added `seed-models-upload`.
- `template.yaml`
  - Added `ModelsBucketName` output.
- `control_plane/backends/aws/compute.py`
  - Added `/diogenes/coldstart` log group stream.
  - Added timestamped `/var/log/diogenes-coldstart.log`.
  - Logs model download/prebaked path start/end and service start.
  - Changed new instance gp3 provisioning to `3000 IOPS / 500 MiB/s`.
- Tests:
  - Added `tests/unit/test_aws_compute.py`.
  - `uv run --no-sync pytest tests/unit -q` passed with `35 passed`.

## Upload Status

- Tried starting Q4_K_M upload with:
  - `AWS_REGION=us-east-2 STACK_NAME=diogenes PYTHONUNBUFFERED=1 uv run --extra upload python -u scripts/seed_models.py --upload`
- It printed:
  - `Uploading 2 model(s) to s3://diogenes-models-dev-265978616089/...`
  - `Qwen/Qwen3.5-27B: downloading bartowski/Qwen_Qwen3.5-27B-GGUF/Qwen_Qwen3.5-27B-Q4_K_M.gguf from HuggingFace...`
- Final bucket check before stopping:
  - `Total Objects: 0`
  - `Total Size: 0 Bytes`
- So treat the S3 upload as not completed.
- Follow-up: the laptop-based Qwen 3.5 27B download was stopped after deciding to switch
  the default 27B model to Qwen 3.6 and move model sync into CodeBuild.

## Model Sync Pipeline Changes

- Added `models.json` as the source of truth for available models.
- Updated the 27B model to:
  - API model name: `Qwen/Qwen3.6-27B`
  - GGUF repo: `unsloth/Qwen3.6-27B-GGUF`
  - GGUF file: `Qwen3.6-27B-Q4_K_M.gguf`
- Kept the 4B model on `Qwen/Qwen3.5-4B`.
- `scripts/seed_models.py` now reads the manifest, uses fast Xet defaults for HF downloads,
  uploads missing files to S3, and seeds DynamoDB with `s3_key`.
- `template.yaml` now creates a CodeBuild model sync project.
- `scripts/deploy.sh` uploads `models.json` and `scripts/seed_models.py` to the model bucket
  and starts the CodeBuild sync after `sam deploy`.
- Optional deploy env vars:
  - `HF_TOKEN_SECRET_ARN`: Secrets Manager secret ARN exposed as `HF_TOKEN` to CodeBuild.
  - `SYNC_MODELS_ON_DEPLOY=0`: deploy stack without triggering model sync.

## Verification

- `UV_CACHE_DIR=/tmp/uv-cache AWS_REGION=us-east-2 MODELS_BUCKET=example-bucket uv run --no-sync python scripts/seed_models.py --use-s3 --dry-run`
- `UV_CACHE_DIR=/tmp/uv-cache uv run --no-sync pytest tests/unit -q`
  - `38 passed`
- `bash -n scripts/deploy.sh`
- `sam validate --lint`
  - valid SAM template

## Local Model Note

- Local Downloads contains:
  - `/Users/wtj/Downloads/Qwen3.5-27B-GGUF/Qwen3.5-27B-Q4_K_S.gguf` (~15 GB)
  - `/Users/wtj/Downloads/Qwen3.5-27B-GGUF/mmproj-F32.gguf` (~1.7 GB)
- This is Q4_K_S, not Q4_K_M. Decision was to keep Q4_K_M as default unless explicitly choosing lower quality for speed.

## Next Steps

1. Deploy the updated stack and trigger CodeBuild model sync.
   - Recommended: create a Secrets Manager secret containing the HF token and deploy with
     `HF_TOKEN_SECRET_ARN=<secret-arn>`.
   - The sync job should upload `Qwen3.6-27B-Q4_K_M.gguf` and
     `Qwen_Qwen3.5-4B-Q4_K_M.gguf` to `s3://diogenes-models-dev-265978616089/`,
     then seed DynamoDB with `s3_key`.
2. Rebuild/deploy AMI pipeline from repo recipe `1.3.9` so new AMIs do not contain baked GGUF files.
3. Trigger a fresh cold start for `Qwen/Qwen3.6-27B`.
4. Compare timings from:
   - `/diogenes/coldstart`
   - `/diogenes/vllm`
   - DynamoDB instance timestamps
5. If S3 fresh-write is still too slow, implement model-level `cold_start_policy`:
   - `terminate`: current cheapest behavior.
   - `stop`: stop instance and keep EBS volume warm.
   - `keep_warm`: skip scale-down.
6. For `stop` policy, add `ComputeBackend.stop/start` and state `stopped`; update `scale_up` to start existing stopped instances before launching new ones.
