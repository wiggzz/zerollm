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
- Stack was updated successfully after `29927cf Add remote model sync pipeline`.
- API URL: `https://c64j0mm1c4.execute-api.us-east-2.amazonaws.com`
- Model bucket output: `diogenes-models-dev-265978616089`
- Current deployed AMI parameter: `ami-0750beefa394b06e9`
- AMI pipeline stack is still on recipe `1.3.8`; repo AMI template is `1.3.9`.
- Model sync project: `diogenes-model-sync-dev`
- HF token secret used for sync: `arn:aws:secretsmanager:us-east-2:265978616089:secret:diogenes/hf-token-dev-6yDEeu`

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
- Deployed with:
  - `AWS_REGION=us-east-2 STACK_NAME=diogenes HF_TOKEN_SECRET_ARN=<secret-arn> ./scripts/deploy.sh`
- CodeBuild sync `diogenes-model-sync-dev:993fe4e8-b33c-4798-9085-689506925cfc`:
  - Downloaded and uploaded `Qwen3.6-27B-Q4_K_M.gguf` (`15.7 GiB`) to S3.
  - Downloaded and uploaded `Qwen_Qwen3.5-4B-Q4_K_M.gguf` (`2.7 GiB`) to S3.
  - Seeded `Qwen/Qwen3.6-27B` and `Qwen/Qwen3.5-4B`.
- CodeBuild sync `diogenes-model-sync-dev:0686fad2-f398-42af-9ba2-c90e52ae912f`:
  - Skipped both existing GGUF objects.
  - Pruned stale DynamoDB model row `Qwen/Qwen3.5-27B`.
- Current model table contains exactly:
  - `Qwen/Qwen3.6-27B`
  - `Qwen/Qwen3.5-4B`
- `/v1/models` returned those two models.
- Ready-state chat request returned HTTP 200 through the deployed API.

## Qwen 3.6 Cold Start Timing

- Instance: `i-012f2570c1fbc37ab`
- Public IP during validation: `3.145.168.227`
- Cold-start log timings:
  - `cloud_init_start`: `2026-04-25T20:14:33Z`
  - `model_download_start`: `2026-04-25T20:14:47Z`
  - `model_download_done`: `2026-04-25T20:16:33Z`
  - `llama_service_start`: `2026-04-25T20:16:33Z`
  - llama-server listening: `2026-04-25T20:17:05Z`
- S3 download duration: about 106 seconds for `16,817,244,384` bytes.
- Total launch-record age when status script first showed `ready`: about 5-6 minutes.
- Readiness is currently discovered by EventBridge polling, so DynamoDB can lag actual
  llama-server readiness by up to about 60 seconds.

## Qwen 3.6 Memory / Context Notes

- Current runtime args for 27B:
  - Previous `g5.2xlarge` default: `-ngl 99 --ctx-size 32768 --parallel 1 --jinja`
  - Updated `g6e.2xlarge` default: `-ngl 99 --ctx-size 262144 --parallel 1 --jinja --cache-ram 40960`
- Startup memory logs at 32k context:
  - GPU: NVIDIA A10G, about `22587 MiB` total VRAM.
  - llama.cpp projected use: `18038 MiB` device memory.
  - Projected free headroom: `3665 MiB`.
  - GPU model buffer: `15345.66 MiB`.
  - KV cache: `2048.00 MiB`.
  - Recurrent/state buffer: `149.62 MiB`.
  - CUDA compute buffer: `495.00 MiB`.
- Rough context scaling:
  - `64k` should add about `2 GiB` KV cache and may fit on the current A10G.
  - `96k`/`128k` likely need a smaller quant or more VRAM.
  - Full `262k` model context is not realistic on A10G for this 27B quant.
- AWS more-VRAM options within 32 vCPUs to vet:
  - `g6e.xlarge`: 4 vCPUs, one L40S-class GPU, 48 GB advertised GPU memory.
  - `g6e.2xlarge`: 8 vCPUs, one L40S-class GPU, 48 GB advertised GPU memory.
  - `g6e.4xlarge`: 16 vCPUs, one L40S-class GPU, 48 GB advertised GPU memory.
  - `g6e.8xlarge`: 32 vCPUs, one L40S-class GPU, 48 GB advertised GPU memory.
  - Practical usable VRAM will be lower after driver/runtime overhead, but this is still
    roughly 2x the current single A10G `g5.2xlarge` class.
- Next suggested experiment:
  - Cold-start `Qwen/Qwen3.6-27B` on `g6e.2xlarge` with `--ctx-size 262144`
    and verify llama-server's projected memory, prompt ingestion speed, and first-token
    latency under realistic long-context prompts.
  - Keep `--parallel 1` for personal use and maximum context headroom.
  - Verify prompt cache behavior with realistic agentic turns. Earlier logs showed
    `cache state: 1 prompts, 11565.773 MiB` at about 73k prompt tokens while the default
    prompt-cache RAM limit was `8192 MiB`, so `--cache-ram 40960` is the conservative
    first pass for keeping most of a 230k-ish token working set cached on `g6e.2xlarge`.

### Quick EC2 price comparison

- Region/pricing basis: `us-east-1`, Linux shared-tenancy On-Demand, AWS Pricing API
  publication `2026-04-27T07:13:25Z`.

| Instance | vCPU | RAM | GPU | GPU memory | On-Demand $/hr | 730h $/mo | vs `g5.2xlarge` |
| --- | ---: | ---: | --- | ---: | ---: | ---: | ---: |
| `g5.2xlarge` | 8 | 32 GiB | 1x A10G | 24 GB | 1.21200 | 884.76 | 1.00x |
| `g6e.xlarge` | 4 | 32 GiB | 1x L40S | 48 GB | 1.86100 | 1,358.53 | 1.54x |
| `g6e.2xlarge` | 8 | 64 GiB | 1x L40S | 48 GB | 2.24208 | 1,636.72 | 1.85x |
| `g6e.4xlarge` | 16 | 128 GiB | 1x L40S | 48 GB | 3.00424 | 2,193.10 | 2.48x |
| `g6e.8xlarge` | 32 | 256 GiB | 1x L40S | 48 GB | 4.52856 | 3,305.85 | 3.74x |

- Best first larger-instance candidate: `g6e.2xlarge`. It preserves the current 8 vCPU
  shape while doubling advertised GPU memory and system RAM.
- Cheapest 48 GB VRAM candidate: `g6e.xlarge`, but the vCPU downgrade from 8 to 4 could
  hurt prompt processing, model loading, and general llama.cpp overhead.
- `g6e.4xlarge` is the likely next step if `g6e.2xlarge` has CPU-side bottlenecks or if
  `--parallel 2` needs more host headroom. `g6e.8xlarge` should be a later escalation.

## Current Runtime Issue

- User reported client-side errors:
  - `Error: 503 status code (no body)`
- Findings:
  - DynamoDB row for `Qwen/Qwen3.6-27B` was `ready`.
  - Direct instance `/health` returned HTTP 200 with `{"status":"ok"}`.
  - llama-server logs showed upstream `POST /v1/chat/completions` responses with HTTP 200.
  - Router Lambda logs showed several invocations hitting the full `120000 ms` timeout.
- Working theory:
  - The Lambda router buffers the full upstream response. Long generations, streaming-like
    client behavior, Qwen thinking/reasoning, or queueing behind `--parallel 1` can outlive
    the Lambda/API Gateway request budget and surface as gateway-level 503s with no app body.
- Previous code detail:
  - The removed buffered `RouterFunction` was configured with `Timeout: 120`.
  - Its `proxy_request` called `requests.post(..., timeout=120)`.
  - This left no budget for controlled error handling when upstream generation approached
    the Lambda limit, so API Gateway/Lambda could synthesize a bodyless 503.
- Streaming fix direction:
  - Implemented direction: inference now uses a Node.js Lambda Function URL with
    `InvokeMode: RESPONSE_STREAM`.
  - The previous `AWS::Serverless::HttpApi` + Python Lambda inference path was removed
    instead of kept as a deprecated non-streaming endpoint.
  - The streaming function validates the same `dio-` API keys, queries DynamoDB for the
    ready instance, triggers scale-up on cold models, and pipes upstream response chunks
    without buffering the full completion.
  - Preferred client endpoint for new work: `POST /v1/responses` on the `StreamingApiUrl`
    stack output. llama.cpp now documents `/v1/responses`; chat completions remain as
    compatibility fallback, and legacy completions are not exposed.
- Added related TODOs, marked as needing vetting:
  - Qwen thinking/reasoning defaults.
  - `--parallel 1` queueing tradeoff.
  - Cold-start readiness lag, stop/start, keep-warm, and phase reporting.

## Local Model Note

- Local Downloads contains:
  - `/Users/wtj/Downloads/Qwen3.5-27B-GGUF/Qwen3.5-27B-Q4_K_S.gguf` (~15 GB)
  - `/Users/wtj/Downloads/Qwen3.5-27B-GGUF/mmproj-F32.gguf` (~1.7 GB)
- This is Q4_K_S, not Q4_K_M. Decision was to keep Q4_K_M as default unless explicitly choosing lower quality for speed.

## Next Steps

1. Vet and prioritize the Lambda timeout/streaming issue before relying on the 27B model
   for long or streaming requests.
2. Rebuild/deploy AMI pipeline from repo recipe `1.3.9` so new AMIs do not contain baked GGUF files.
3. Warm stop/start is now implemented:
   - idle `ready` instances stop by default instead of terminating.
   - stopped instances keep EBS warm for `warm_timeout` seconds; default is 8 hours.
   - `scale_up` starts a warm stopped instance before launching a new one.
   - streaming requests mark the instance `busy`, and scale-down skips active requests.
4. If cold start still needs improvement, consider measuring stop/start boot + model load separately from fresh launch + model sync.
