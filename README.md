# ZeroLLM

[![CI](https://github.com/wiggzz/zerollm/actions/workflows/ci.yml/badge.svg?branch=main)](https://github.com/wiggzz/zerollm/actions/workflows/ci.yml)
[![AWS Smoke](https://github.com/wiggzz/zerollm/actions/workflows/aws-smoke.yml/badge.svg?branch=main)](https://github.com/wiggzz/zerollm/actions/workflows/aws-smoke.yml)

```text
███████╗███████╗██████╗  ██████╗ ██╗     ██╗     ███╗   ███╗
╚══███╔╝██╔════╝██╔══██╗██╔═══██╗██║     ██║     ████╗ ████║
  ███╔╝ █████╗  ██████╔╝██║   ██║██║     ██║     ██╔████╔██║
 ███╔╝  ██╔══╝  ██╔══██╗██║   ██║██║     ██║     ██║╚██╔╝██║
███████╗███████╗██║  ██║╚██████╔╝███████╗███████╗██║ ╚═╝ ██║
╚══════╝╚══════╝╚═╝  ╚═╝ ╚═════╝ ╚══════╝╚══════╝╚═╝     ╚═╝
```

ZeroLLM is a personal LLM backend control plane that runs open models on GPU EC2 instances and scales idle capacity down.

Current status:

- AWS orchestration, streaming inference, API key auth, model sync, and cluster state are implemented.
- Inference uses a Lambda Function URL with response streaming.
- GPU instances run `llama-server`; some internal names still say `vLLM`.
- Google OAuth and the web UI are not implemented.

## Quickstart

### Prerequisites

- Python 3.12+
- `uv`
- Docker for LocalStack E2E tests
- AWS SAM CLI for deploy/build commands

### Install

```bash
make setup-dev
```

### Test

```bash
make test-unit
make test-e2e
```

E2E tests use Docker/Testcontainers and skip when that environment is unavailable.

### Validate / Build

```bash
make validate
make build
```

## Deploy

```bash
AWS_REGION=ap-southeast-2 make deploy
```

`make deploy` wraps `scripts/deploy.sh`. It can:

- select or build a GPU AMI from the Image Builder pipeline
- discover and pin GPU subnet/VPC parameters
- create/update the SAM stack
- upload `models.json` and `scripts/seed_models.py`
- trigger the CodeBuild model sync project

Common deploy variables:

- `STACK_NAME` - default `zerollm`
- `ENVIRONMENT` - default `dev`
- `DEPLOY_DEFAULTS_FILE` - default `.zerollm/deploy-<region>-<stack>.env`
- `CFN_ROLE_ARN` - CloudFormation execution role ARN
- `AMI_BUILD_MODE=auto|latest|build` - default `auto`
- `GPU_AMI_ID`, `GPU_SUBNET_ID`, `GPU_VPC_ID` - override auto-discovery
- `HF_TOKEN_SECRET_ARN` - optional Secrets Manager ARN exposed as `HF_TOKEN` to model sync
- `SYNC_MODELS_ON_DEPLOY=0` - skip model sync

After deploy, create an API key:

```bash
AWS_REGION=ap-southeast-2 make create-api-key EMAIL=you@example.com
```

## Usage

Use the `StreamingApiUrl` stack output as the OpenAI-compatible base URL:

```text
https://<function-url-id>.lambda-url.<region>.on.aws/v1
```

Send API keys as:

```text
Authorization: Bearer zllm-...
```

Supported routes:

- `POST /v1/responses`
- `POST /v1/chat/completions`
- `GET /v1/models`

First inference for a cold model currently returns a cold-start response and triggers scale-up; clients should retry after the suggested delay.

## Models

`models.json` is the default manifest. It pins Hugging Face revisions and GGUF filenames, then model sync uploads artifacts to the deployment model bucket and writes DynamoDB model rows.

Default models:

- `Qwen/Qwen3.6-27B` on `g6e.2xlarge`
- `Qwen/Qwen3.5-4B` on `g5.xlarge`

## Useful Commands

- `make setup` - install runtime dependencies
- `make setup-dev` - install runtime and dev dependencies
- `make test` - run the default test target
- `make test-unit` - run unit tests
- `make test-e2e` - run LocalStack/mock-server E2E tests
- `make validate` - validate the SAM template
- `make build` - sync requirements and run `sam build`
- `make deploy` - deploy/update the AWS stack
- `make seed-models` - seed model configs
- `make seed-models-upload` - upload model files and seed configs
- `make create-api-key EMAIL=you@example.com` - create an API key
- `make status` - print cluster records
- `make logs` - show EC2 health and instance logs via SSM

## Pi Provider Example

Add a provider in `~/.pi/agent/models.json`:

```json
{
  "providers": {
    "zerollm": {
      "baseUrl": "https://<streaming-url>.lambda-url.<region>.on.aws/v1",
      "api": "openai-completions",
      "apiKey": "<zllm-key>",
      "models": [
        { "id": "Qwen/Qwen3.5-4B", "contextWindow": 131072, "reasoning": true, "compat": { "thinkingFormat": "deepseek" } },
        { "id": "Qwen/Qwen3.6-27B", "contextWindow": 262144, "reasoning": true, "compat": { "thinkingFormat": "deepseek" } }
      ]
    }
  }
}
```

Set the default in `~/.pi/agent/settings.json`:

```json
{
  "defaultProvider": "zerollm",
  "defaultModel": "Qwen/Qwen3.6-27B",
  "defaultThinkingLevel": "medium"
}
```

## Repository Layout

- `control_plane/core/` - cloud-agnostic lifecycle and API logic
- `control_plane/backends/aws/` - AWS backends and Lambda handlers
- `control_plane/backends/mock/` - in-memory test backends
- `ami/` - GPU AMI Image Builder assets
- `scripts/` - deploy, model sync, API key, status, and log helpers
- `tests/unit/` - fast unit tests
- `tests/e2e/` - LocalStack/mock-server E2E tests
- `infrastructure/` - Terraform bootstrap for GitHub Actions AWS roles
