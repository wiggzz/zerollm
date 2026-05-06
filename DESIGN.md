# ZeroLLM

> *"He has the most who is content with the least."*

A personal LLM backend that scales to zero. Zero cost when idle, GPU inference when you need it.

## Overview

ZeroLLM is a self-hosted, serverless LLM inference platform. It provides an OpenAI-compatible API backed by open-source models running on on-demand GPU instances. When not in use, the entire system scales to zero — you pay nothing. When a request comes in, the control plane provisions GPU capacity, loads a model, and serves inference. A simple web UI lets you monitor cluster state and chat with models.

## Goals

- **Zero cost at rest**: No running instances, no idle GPUs, no charges when not in use
- **OpenAI-compatible API**: Drop-in replacement for OpenAI client libraries
- **Single-command deploy**: `sam deploy` (or equivalent) provisions the entire stack
- **Open model support**: Run any Hugging Face / vLLM-compatible model
- **Tunable GPU sizing**: Choose model + GPU instance type pairings
- **Simple UI**: View cluster state (what's running, what's scaling) and chat with models
- **Cloud-portable design**: AWS-first, but architected so compute backends can be swapped

## Non-Goals

- Fast cold start (minutes of scale-up latency is acceptable)
- Training / fine-tuning (inference only)
- Competing with managed services on latency or throughput

## Authentication

ZeroLLM uses Google OAuth 2.0 for identity, with an email allowlist for access control.

### How It Works

1. **Allowed users** are defined in the SAM config as a list of email addresses:
   ```yaml
   # samconfig.toml
   [default.deploy.parameters]
   parameter_overrides = "AllowedEmails=alice@gmail.com,bob@example.com"
   ```
   These are stored as a comma-separated SSM parameter (or directly in the Lambda environment) at deploy time.

2. **Web UI auth flow** (Google Sign-In):
   - User clicks "Sign in with Google" → Google OAuth consent screen
   - Google returns an ID token (JWT) to the frontend
   - Frontend sends the ID token in `Authorization: Bearer <id_token>` on all API requests
   - A **Lambda Authorizer** on API Gateway validates the token:
     1. Verify JWT signature against Google's public keys (JWKS)
     2. Check `iss`, `aud` (must match your Google Client ID), and `exp`
     3. Extract `email` claim and check it against the allowlist
     4. Return an IAM policy (allow/deny)
   - Authorizer responses are cached (5 min TTL) to avoid re-validating on every request

3. **Programmatic access** (OpenAI client compatibility):
   - Users can generate **API keys** via the UI (stored hashed in DynamoDB)
   - API keys are sent as `Authorization: Bearer zllm-xxxxxxxxxxxx`
   - The Lambda Authorizer detects the `zllm-` prefix and validates against DynamoDB instead of Google
   - This lets you use standard OpenAI clients:
     ```python
     client = OpenAI(base_url="https://your-api.example.com/v1", api_key="zllm-xxxxxxxxxxxx")
     ```

### Auth Components

- **Google OAuth Client ID**: Created in Google Cloud Console, configured as a SAM parameter
- **Lambda Authorizer**: Single function handling both Google JWT and API key validation
- **DynamoDB ApiKeys table**: Stores hashed API keys, associated email, created at, last used
- **SSM Parameter**: `/zerollm/allowed-emails` — the email allowlist

### DynamoDB ApiKeys Table

| Field | Type | Description |
|---|---|---|
| `key_hash` | S (PK) | SHA-256 hash of the API key |
| `email` | S | Email of the user who created it |
| `name` | S | User-provided label (e.g., "laptop", "server") |
| `created_at` | N | Unix timestamp |
| `last_used_at` | N | Unix timestamp |

## Architecture

```
                    ┌─────────────┐
                    │   Web UI    │
                    │  (S3 + CF)  │
                    └──────┬──────┘
                           │
                    ┌──────▼──────┐
                    │  API Gateway │
                    │  (HTTP API)  │
                    └──────┬──────┘
                           │
                    ┌──────▼──────┐
                    │   Lambda    │
                    │  Authorizer │──── Google JWKS
                    └──────┬──────┘     + DynamoDB (API keys)
                           │
              ┌────────────┼────────────┐
              │            │            │
       ┌──────▼──────┐ ┌──▼───┐ ┌──────▼──────┐
       │   Router     │ │ Chat │ │   Cluster   │
       │   Lambda     │ │ Lambda│ │   State     │
       │              │ │      │ │   Lambda    │
       └──────┬──────┘ └──┬───┘ └──────┬──────┘
              │            │            │
              │      ┌─────▼─────┐      │
              │      │ DynamoDB  │      │
              │      │ (state)   │◄─────┘
              │      └─────┬─────┘
              │            │
       ┌──────▼────────────▼──────┐
       │     Orchestrator Lambda   │
       │  (scale-up / scale-down)  │
       └──────┬───────────┬───────┘
              │           │
       ┌──────▼──┐  ┌─────▼─────┐
       │  EC2 GPU │  │  EC2 GPU  │
       │ Instance │  │  Instance │
       │ (vLLM)  │  │  (vLLM)   │
       └─────────┘  └───────────┘
```

### Components

#### 1. API Gateway (AWS HTTP API) and Streaming Function URL

The Lambda Function URL is the inference entry point. Routes:
- `POST /v1/messages` — Anthropic Messages API
- `POST /v1/responses` — OpenAI-compatible Responses API
- `POST /v1/chat/completions` — compatibility fallback
- `GET /v1/models` — List available models

The API Gateway URL is the control-plane entry point. Routes:
- `GET /api/cluster` — Cluster state for the UI
- `POST /api/cluster/scale` — Manual scale up/down
- `POST /api/keys` — Create an API key (returns the key once)
- `GET /api/keys` — List API keys (metadata only, not the key itself)
- `DELETE /api/keys/{key_id}` — Revoke an API key

#### 2. Streaming Router Lambda

Receives inference requests. Checks DynamoDB for a running instance with the requested model. If one exists, proxies the request. If not, enqueues a scale-up request and returns a `503` with `Retry-After` header (client polls).

Alternative: hold the connection open (up to API Gateway's 30s timeout), poll for instance readiness, and proxy once available. For longer cold starts, return a streaming response with an initial "warming up" event.

#### 3. Orchestrator Lambda

Triggered by scale-up requests (via SQS or direct invocation). Responsibilities:
- Launch EC2 GPU instances with a pre-baked AMI (vLLM + model weights cached on EBS snapshot)
- Poll instance health until the vLLM server is ready
- Register the instance in DynamoDB
- Handle scale-down: a scheduled EventBridge rule invokes this Lambda periodically to terminate instances that have been idle beyond a configurable timeout

#### 4. GPU Instances (EC2)

- Run vLLM serving an OpenAI-compatible API
- Pre-baked AMI with NVIDIA drivers, vLLM, and CUDA
- Model weights loaded from an EBS snapshot (fast attach) or pulled from S3/HF on boot
- Report health via a simple HTTP endpoint that the orchestrator polls
- Instance types are configurable per model (e.g., `g5.xlarge` for 7B, `g5.12xlarge` for 70B)

#### 5. DynamoDB (State Store)

Tables:
- **Instances**: instance ID, model, status (starting/ready/draining/terminated), IP, launched at, last request at
- **Models**: model name, instance type, vLLM args, EBS snapshot ID, desired count, idle timeout
- **ApiKeys**: key hash (PK), email, name, created at, last used at

#### 6. Web UI (S3 + CloudFront)

Static SPA (React or similar). Features:
- Dashboard: list of configured models, their status (cold/warming/ready), instance count
- Chat: simple chat interface, model picker
- Config: edit model configs (instance type, idle timeout)

Deployed as static files to S3, served via CloudFront. Google Sign-In button for auth; includes an API keys management page.

### Scale-Up Flow

```
1. Client sends POST /v1/chat/completions {model: "meta-llama/Llama-3.1-8B"}
2. Router Lambda checks DynamoDB for a ready instance serving that model
3. No instance found → Router pushes scale-up request to SQS
4. Router returns 503 + Retry-After: 120
5. Orchestrator Lambda picks up SQS message
6. Orchestrator launches EC2 g5.xlarge from pre-baked AMI
7. Orchestrator polls instance health endpoint every 10s
8. Instance reports ready (~2-5 min)
9. Orchestrator writes instance record to DynamoDB (status: ready)
10. Client retries → Router finds ready instance → proxies request → returns response
```

### Scale-Down Flow

```
1. EventBridge rule fires every 1 minute
2. Orchestrator Lambda scans DynamoDB for instances where:
   last_request_at < now() - idle_timeout
3. For each idle instance:
   a. Set status to "draining"
   b. Wait for in-flight requests to complete (or timeout)
   c. Terminate EC2 instance
   d. Set status to "terminated"
```

## Model Configuration

Models are defined in a config file (or DynamoDB, seeded at deploy time):

```yaml
models:
  # RECOMMENDED DEFAULT — best open-source coding model
  # 70.6% SWE-bench Verified, 3B active params (MoE), 256k context
  # Use native FP8 weights (not quantized) for best quality
  # 80B total params in FP8 ≈ ~80GB → needs 4x A10G
  - name: "Qwen/Qwen3-Coder-Next"
    instance_type: "g5.12xlarge"      # 4x A10G, 96GB VRAM
    idle_timeout: 300                  # seconds
    vllm_args: "--tensor-parallel-size 4 --max-model-len 65536 --enable-auto-tool-choice --tool-call-parser qwen3_coder"

  # Best value for general chat/Q&A at ~$1/hr
  # Qwen3 32B matches Qwen2.5 72B on STEM, coding, reasoning
  - name: "Qwen/Qwen3-32B"
    instance_type: "g5.xlarge"        # 1x A10G, 24GB VRAM
    idle_timeout: 300
    vllm_args: "--max-model-len 32768"

  # Best open-source reasoning model in the 70B class
  # 94.5% on MATH-500, 57.5 on LiveCodeBench
  - name: "deepseek-ai/DeepSeek-R1-Distill-Llama-70B"
    instance_type: "g5.12xlarge"      # 4x A10G, 96GB VRAM
    idle_timeout: 300
    vllm_args: "--tensor-parallel-size 4 --max-model-len 8192 --quantization awq"
```

### GPU Sizing Reference

| Instance | GPUs | VRAM | $/hr | Good For |
|---|---|---|---|---|
| g5.xlarge | 1x A10G | 24 GB | ~$1.01 | 8B-32B models |
| g5.12xlarge | 4x A10G | 96 GB | ~$5.67 | 70B models (quantized) |
| g5.48xlarge | 8x A10G | 192 GB | ~$16.29 | 70B (full precision), large MoE |
| p4d.24xlarge | 8x A100 40GB | 320 GB | ~$32.77 | 405B (quantized) |
| p4de.24xlarge | 8x A100 80GB | 640 GB | ~$40.97 | 405B+, DeepSeek R1 full |

### Model Quality vs Cost

For reference, here's how self-hosted open models compare to frontier closed APIs:

| Model | SWE-bench Verified | Cost | Notes |
|---|---|---|---|
| Claude Opus 4.6 | 80.8% | $15/$75 per M tokens | Best agentic coding |
| GPT-5.2 | 80.0% | $10/$30 per M tokens | Strong reasoning |
| **Qwen3-Coder-Next** | **70.6%** | **~$5.67/hr (self-hosted)** | **Best open-source for coding; 3B active MoE** |
| DeepSeek-V3.2-Speciale | 73.1% | ~$33/hr (self-hosted) | Needs p4d+, expensive |
| Kimi-Dev-72B | 60.4% | ~$5.67/hr (self-hosted) | Dense 72B, strong coding |
| Qwen3 32B | ~33% | ~$1/hr (self-hosted) | Best value for general use |
| DeepSeek R1 Distill 70B | — | ~$5.67/hr (self-hosted) | Best open-source reasoning |

Qwen3-Coder-Next at 70.6% SWE-bench is within striking distance of frontier closed
models (80%) and is the clear best choice for self-hosted coding. Early community
feedback is positive — developers report it can one-shot moderately complex features
(ACL systems, game clones) and works well with agentic scaffolds (SWE-Agent, Cline,
Claude Code). Caveats: tool-calling reliability is weaker than frontier models, and
aggressive quantization (Q2/Q4) degrades quality significantly — use native FP8 weights.

For general chat, summarization, and Q&A, Qwen3 32B at ~$1/hr is the best value pick.

## Tech Stack

| Component | Technology |
|---|---|
| IaC | AWS SAM (CloudFormation) |
| Control plane | Python Lambda functions |
| State store | DynamoDB |
| Inference | vLLM on EC2 GPU instances |
| GPU AMI | Packer (or manual, to start) |
| API | API Gateway HTTP API |
| UI | React (Vite), hosted on S3 + CloudFront |
| Queue | SQS (for scale-up requests) |
| Scheduler | EventBridge (for scale-down checks) |

## Project Structure

```
zerollm/
├── DESIGN.md
├── template.yaml              # SAM template
├── samconfig.toml             # SAM config
├── control-plane/
│   ├── authorizer/            # Lambda Authorizer (Google JWT + API keys)
│   │   └── handler.py
│   ├── router/                # Router Lambda
│   │   └── handler.py
│   ├── orchestrator/          # Orchestrator Lambda
│   │   └── handler.py
│   ├── cluster/               # Cluster state Lambda
│   │   └── handler.py
│   ├── keys/                  # API key management Lambda
│   │   └── handler.py
│   └── shared/                # Shared utilities
│       ├── models.py
│       └── db.py
├── ami/
│   └── setup.sh               # GPU instance setup script
├── ui/
│   ├── package.json
│   ├── src/
│   └── public/
└── tests/
    ├── unit/
    └── integration/
```

## Deployment

```bash
# First time: build the GPU AMI (one-time, ~15 min)
cd ami && ./build.sh

# Deploy the stack
sam build && sam deploy --guided

# Subsequent deploys
sam build && sam deploy
```

## Cost Analysis

**At rest (zero traffic):**
- API Gateway: $0
- Lambda: $0
- DynamoDB: $0 (on-demand, no reads/writes)
- S3 + CloudFront: ~$0.50/month (static hosting)
- **Total: ~$0.50/month**

**Active (Qwen3-Coder-Next on g5.12xlarge):**
- EC2 g5.12xlarge: ~$5.67/hour
- EBS: ~$0.08/GB/month
- API Gateway + Lambda: negligible
- **Total: ~$5.67/hour while in use**

**Active (Qwen3 32B on g5.xlarge):**
- EC2 g5.xlarge: ~$1.01/hour
- EBS: ~$0.08/GB/month
- API Gateway + Lambda: negligible
- **Total: ~$1/hour while in use**

## Future Extensions

- **Multi-node pipeline parallelism**: Run larger models (405B, DeepSeek R1 full) across multiple instances using vLLM + Ray. Use tensor parallelism within each node and pipeline parallelism across nodes (e.g., 2x g5.12xlarge → `--tensor-parallel-size 4 --pipeline-parallel-size 2`). Requires Ray cluster orchestration, VPC networking, and coordinated startup — all nodes must be healthy before inference begins.
- **Other clouds**: Abstract the compute backend (GCP, Azure, Lambda Labs, RunPod)
- **Spot instances**: Use spot for even cheaper GPU compute (with interruption handling)
- **Model caching**: Keep warm EBS volumes for faster restarts
- **Multi-model per instance**: Run smaller models on the same GPU
- **Usage tracking**: Log tokens consumed, cost per session
- **Additional auth providers**: GitHub, email/password, SAML
- **Closed API passthrough**: Proxy requests to Claude/GPT APIs for tasks that exceed open model capabilities, with unified auth and usage tracking
