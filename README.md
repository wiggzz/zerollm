# ZeroLLM

```text
███████╗███████╗██████╗  ██████╗ ██╗     ██╗     ███╗   ███╗
╚══███╔╝██╔════╝██╔══██╗██╔═══██╗██║     ██║     ████╗ ████║
  ███╔╝ █████╗  ██████╔╝██║   ██║██║     ██║     ██╔████╔██║
 ███╔╝  ██╔══╝  ██╔══██╗██║   ██║██║     ██║     ██║╚██╔╝██║
███████╗███████╗██║  ██║╚██████╔╝███████╗███████╗██║ ╚═╝ ██║
╚══════╝╚══════╝╚═╝  ╚═╝ ╚═════╝ ╚══════╝╚══════╝╚═╝     ╚═╝
```

ZeroLLM is a personal LLM backend control plane designed to scale GPU inference to zero when idle.

Current status: phases 1-4 are implemented (orchestration, routing, API key auth, and cluster state). The runtime now starts `llama-server` on GPU instances, although some internal names still say `vLLM`.
Inference is exposed through a Lambda Function URL for true streamed responses.

Not yet implemented:
- Google OAuth / JWT validation. Programmatic API keys with the `zllm-` prefix are implemented.
- Web UI.
- Automatic model seeding during deploy.

## What Is Included

- AWS SAM template and Lambda handlers (`template.yaml`, `control_plane/backends/aws/handlers.py`)
- Cloud-agnostic core logic (`control_plane/core/`)
- AWS and mock backends (`control_plane/backends/aws/`, `control_plane/backends/mock/`)
- Unit and E2E tests (`tests/unit/`, `tests/e2e/`)

## Quickstart

### 1. Prerequisites

- Python 3.12+
- `uv`
- Docker (required for LocalStack E2E tests)
- Optional: AWS SAM CLI (for build/deploy)

### 2. Install Dependencies

```bash
make setup-dev
```

### 3. Run Unit Tests (Fast Feedback)

```bash
make test-unit
```

### 4. Run E2E Tests (LocalStack + Mock vLLM)

```bash
make test-e2e
```

E2E tests require Docker. LocalStack tests are skipped if Docker/Testcontainers is unavailable.

### 5. Optional: Build/Validate SAM Template

```bash
make validate
make build
```

### 5b. One-Command Deploy (Auto Params)

```bash
AWS_REGION=ap-southeast-2 make deploy
```

This command automatically:
- builds/uses a GPU AMI from the Image Builder pipeline
- discovers `GpuSubnetId` values and the VPC for those subnets
- creates the GPU security group from the SAM stack
- runs `sam build` and `sam deploy` with parameter overrides

Optional deploy environment variables:
- `STACK_NAME` (default `zerollm`)
- `ENVIRONMENT` (default `dev`)
- `DEPLOY_DEFAULTS_FILE` (default `.zerollm/deploy-<region>-<stack>.env`)
- `CFN_ROLE_ARN`: CloudFormation execution role ARN to use for stack updates
- `AMI_BUILD_MODE=auto` (default): use latest pipeline AMI, build if missing
- `AMI_BUILD_MODE=latest`: require latest pipeline AMI
- `AMI_BUILD_MODE=build`: always build a new AMI first
- `GPU_AMI_ID`, `GPU_SUBNET_ID` to override auto-discovery
- `ALLOWED_EMAILS`, `GOOGLE_CLIENT_ID` for auth configuration

Network defaults behavior:
- First deploy auto-discovers subnet IDs and writes them to `DEPLOY_DEFAULTS_FILE`.
- Later deploys reuse those pinned values by default for consistency.
- Delete the file (or set explicit `GPU_SUBNET_ID`) to re-select.

After deploy, seed model configs and create at least one API key:

```bash
AWS_REGION=ap-southeast-2 make seed-models
AWS_REGION=ap-southeast-2 make create-api-key EMAIL=you@example.com
```

`make seed-models` can also upload configured GGUF files to the deployment S3 bucket when run with the script's `--upload --bucket <bucket>` options.

### 6. Build a GPU AMI (Image Builder)

```bash
AWS_REGION=us-east-1 \
make ami-build
```

Optional environment variables:
- `BASE_AMI_ID` (if omitted, script uses regional defaults when available)
- `BUILDER_SUBNET_ID` (if omitted, script auto-selects a subnet)
- `BUILDER_SECURITY_GROUP_ID` (if omitted, script auto-selects a security group in the subnet VPC)
- `BUILDER_INSTANCE_TYPE` (default `t3.small`, used only for AMI build instances)
- `IMAGE_VERSION` (default `1.0.2`; bump when recipe changes)
- `AMI_PIPELINE_STACK` (default `zerollm-ami-pipeline`)
- `AMI_PIPELINE_ENV` (default `dev`)
- `PIPELINE_STATUS` (default `DISABLED`)

Useful subcommands:

```bash
AWS_REGION=us-east-1 make ami-build-deploy  # deploy/update pipeline stack only
AWS_REGION=us-east-1 make ami-build-start   # start a new image build
AWS_REGION=us-east-1 make ami-build-latest  # print latest AMI ID from pipeline
```

Regional defaults (community-maintained, PRs welcome):

| Region | Base GPU AMI (`BASE_AMI_ID`) | Notes |
|---|---|---|
| `ap-southeast-2` | `ami-021000ae4658b3c28` | Seed default; validate periodically |
| `us-west-2` | `ami-0a08f4510bfe41148` | Seed default; validate periodically |

## Common Commands

- `make setup` - install runtime deps
- `make setup-dev` - install runtime + dev deps
- `make sync-requirements` - regenerate root `requirements.txt` from root `pyproject.toml`
- `make ami-build` - deploy Image Builder stack and build a GPU AMI
- `make ami-build-deploy` - deploy/update Image Builder stack only
- `make ami-build-start` - start a new Image Builder pipeline execution
- `make ami-build-latest` - print latest AMI ID built by pipeline
- `make test` - run default test target (`test-unit`)
- `make test-unit` - run unit tests
- `make test-e2e` - run E2E tests
- `make validate` - validate SAM template
- `make build` - SAM build
- `make deploy` - one-command auto deploy (AMI + network param auto-resolution + SAM deploy)
- `make seed-models` - seed default model configuration into DynamoDB
- `make create-api-key EMAIL=you@example.com` - create a programmatic API key
- `make status` - print instance records from DynamoDB
- `make logs` - show EC2 state, health, and instance journal logs via SSM

Dependency note:
- Root `pyproject.toml` is the source of truth.
- `make build` runs `make sync-requirements` first so SAM packaging stays in sync.

## Usage Notes

- All API Gateway routes are protected by the Lambda authorizer. Use `Authorization: Bearer <zllm-key>` with keys created by `make create-api-key`.
- First inference for a cold model returns `503` with `Retry-After`; the router triggers async scale-up and clients should retry.
- Use the `StreamingApiUrl` stack output for inference clients. It validates the same `Authorization: Bearer <zllm-key>` API keys and supports `POST /v1/responses`, `POST /v1/chat/completions`, and `GET /v1/models`.
- Prefer OpenAI's Responses API (`POST /v1/responses`) for new clients. Chat completions remain available for compatibility; legacy completions are not exposed.
- GPU instances must expose port `8000`; the generated security group currently opens that port publicly.
- The default model seed data points at GGUF model files for `llama-server`. Ensure the files exist in the AMI or upload them to the model bucket and seed `s3_key`.

## Using with Pi

ZeroLLM works as a pi backend via `~/.pi/agent/models.json`. Add a `zerollm` provider:

```json
{
  "providers": {
    "zerollm": {
      "baseUrl": "https://<your-streaming-url>.lambda-url.<region>.on.aws/v1",
      "api": "openai-completions",
      "apiKey": "<your-zllm-key>",
      "models": [
        { "id": "Qwen/Qwen3.5-4B", "contextWindow": 131072, "reasoning": true, "compat": { "thinkingFormat": "deepseek" } },
        { "id": "Qwen/Qwen3.6-27B", "contextWindow": 262144, "reasoning": true, "compat": { "thinkingFormat": "deepseek" } }
      ]
    }
  }
}
```

Set as default in `~/.pi/agent/settings.json`:

```json
{
  "defaultProvider": "zerollm",
  "defaultModel": "Qwen/Qwen3.6-27B",
  "defaultThinkingLevel": "medium"
}
```

Key points:
- **`api: "openai-completions"`** — llama.cpp's server speaks the OpenAI Chat Completions API. Pi's `openai-completions` handler parses DeepSeek-style `<thinking>` blocks from the response stream.
- **`reasoning: true`** — tells pi the model supports extended thinking. Without this, pi won't send reasoning params and thinking level cycling (`Shift+Tab`) will show "Current model does not support thinking".
- **`defaultThinkingLevel`** — set to `off` by default in pi; change to `medium` or `high` to enable thinking on these models.
- **llama-server flag** — models use `--reasoning-format deepseek` in `vllm_args` (see `models.json`) so the server outputs `<thinking>` tags that pi's openai-completions parser maps to thinking blocks.

## Repository Layout

- `control_plane/core/` - cloud-agnostic domain logic
- `control_plane/backends/aws/` - AWS implementations + Lambda handlers
- `control_plane/backends/mock/` - in-memory/mock implementations for testing
- `tests/unit/` - unit tests with mock backends
- `tests/e2e/` - E2E tests (mock vLLM + optional LocalStack)
- `scripts/create_api_key.py` - API key creation helper
