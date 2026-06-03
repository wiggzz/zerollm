# ZeroLLM Design

ZeroLLM is an AWS-first control plane for running open LLMs on GPU EC2 instances while scaling idle capacity to zero.

## Current Shape

- **Inference endpoint**: Lambda Function URL using `InvokeMode: RESPONSE_STREAM`.
- **Control API**: API Gateway HTTP API for cluster state, manual scale, and API key management.
- **Auth**: `zllm-` API keys stored hashed in DynamoDB. Google OAuth parameters exist, but JWT auth is not implemented.
- **State**: DynamoDB tables for instances, model configs, and API keys.
- **Lifecycle**: an orchestrator Lambda launches, checks, stops, starts, and terminates GPU instances.
- **Runtime**: EC2 GPU instances run `llama-server`. Some older internal names still say `vLLM`.
- **Models**: `models.json` is the source of truth. Deploy uploads the manifest and seed script, then CodeBuild syncs GGUF files into S3 and seeds DynamoDB.
- **Scale down**: idle instances stop first and remain warm for `warm_timeout`; expired warm instances terminate.

## Request Flow

1. A client calls the Streaming Function URL under `/v1`.
2. The router validates the bearer API key against DynamoDB.
3. If a ready instance exists for the requested model, the router streams the upstream `llama-server` response back to the client.
4. If no ready instance exists, the router records/refreshes demand, asynchronously invokes the orchestrator, and currently returns a cold-start response for client retry.
5. EventBridge invokes health checks while instances are starting; the orchestrator marks them ready once `/health` succeeds.
6. EventBridge invokes scale-down periodically; idle ready instances are stopped or terminated according to model policy.

## Components

| Component | Purpose |
| --- | --- |
| `template.yaml` | SAM/CloudFormation stack for APIs, Lambdas, DynamoDB, model bucket, CodeBuild sync, schedules, IAM, and GPU security group |
| `control_plane/core/` | Cloud-agnostic lifecycle, auth, keys, cluster state, and routing decisions |
| `control_plane/backends/aws/` | DynamoDB, EC2, and Lambda handler adapters |
| `control_plane/backends/mock/` | In-memory backends for unit and local E2E tests |
| `control_plane/backends/aws/streaming_router.js` | Streaming inference router for Lambda Function URLs |
| `ami/` | EC2 Image Builder template and helper script for GPU AMIs |
| `scripts/deploy.sh` | Deploy orchestration: AMI selection/build, network parameter discovery, SAM deploy, model sync |
| `scripts/seed_models.py` | Manifest validation, Hugging Face download/upload, and DynamoDB model seeding |

## State Model

Instances have statuses such as `starting`, `ready`, `busy`, `stopping`, `stopped`, and `terminated`.

Model rows include:

- `name`
- `instance_type`
- `model_id`
- `hf_repo`, `hf_revision`, `hf_file`
- `s3_key` when the model artifact is stored in the model bucket
- `vllm_args` for current llama-server runtime flags
- `idle_timeout`
- `warm_timeout`

API key rows store only hashed keys plus metadata.

## Boundaries

Core modules depend on protocols, not AWS SDKs. AWS-specific details live in backend adapters and scripts. A second cloud would still need provider-specific compute, state, deploy, model storage, logs, and network plumbing.

## Known Rough Edges

- Cold starts still require retry behavior instead of a polished wait-until-ready stream.
- The GPU instance port is currently public and protected by the shared server API key.
- Naming still carries old `vllm` terms even though the runtime is `llama-server`.
- First deploy still requires follow-up model seeding/API key steps; this should move behind `zlmctl`.
