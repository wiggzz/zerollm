# ZeroLLM Implementation Plan

## Context

We've designed ZeroLLM — a personal LLM backend that scales to zero on AWS. The design doc is committed. Now we need to implement the full stack: SAM infrastructure, Lambda control plane, GPU AMI, and web UI. The goal is to get to a working MVP as fast as possible.

**Testing strategy**: Use testcontainers (LocalStack) for E2E testing — all Docker lifecycle managed from pytest. A mock vLLM server stands in for real GPU instances, letting us test the entire lifecycle without AWS access.

**Cloud portability**: All AWS-specific code lives behind abstract interfaces. Core business logic depends only on these abstractions.

## Architecture: Core vs Cloud Backends

```
control-plane/
├── core/                      # Cloud-agnostic business logic
│   ├── interfaces.py          # Protocols: StateStore, ComputeBackend
│   ├── orchestrator.py        # Scale-up/down logic
│   ├── router.py              # Request routing, proxy, 503 logic
│   ├── auth.py                # Token/key validation logic
│   ├── keys.py                # API key CRUD logic
│   └── cluster.py             # Cluster state logic
├── backends/
│   ├── aws/
│   │   ├── compute.py         # EC2Backend (implements ComputeBackend)
│   │   ├── state.py           # DynamoDBStateStore (implements StateStore)
│   │   └── handlers.py        # Lambda entry points (thin wrappers)
│   └── mock/
│       ├── compute.py         # MockBackend (for testing)
│       └── state.py           # InMemoryStateStore (for testing)
└── shared/
    └── config.py              # Env var access, backend selection
```

### Key Interfaces (`core/interfaces.py`)

```python
from typing import Protocol

class StateStore(Protocol):
    """Persistent state for instances, models, and API keys."""
    def get_instance(self, instance_id: str) -> dict | None: ...
    def list_instances(self, model: str = None, status: str = None) -> list[dict]: ...
    def put_instance(self, instance: dict) -> None: ...
    def update_instance(self, instance_id: str, **fields) -> None: ...
    def get_model_config(self, model_name: str) -> dict | None: ...
    def list_model_configs(self) -> list[dict]: ...
    def get_api_key(self, key_hash: str) -> dict | None: ...
    def put_api_key(self, key: dict) -> None: ...
    def delete_api_key(self, key_hash: str) -> None: ...
    def list_api_keys(self, email: str) -> list[dict]: ...

class ComputeBackend(Protocol):
    """Launch and terminate GPU instances."""
    def launch(self, model_config: dict) -> tuple[str, str]: ...  # (instance_id, ip)
    def terminate(self, instance_id: str) -> None: ...
```

### How It Works

- **Lambda handlers** (`backends/aws/handlers.py`) are thin: parse event, build deps (DynamoDB state store, EC2 backend), call core logic, format response
- **Core logic** (`core/*.py`) only imports `interfaces.py` — never `boto3` directly
- **Tests** use `backends/mock/` — no moto needed, fast and simple
- **Adding a new cloud** = implement `ComputeBackend` + `StateStore` + thin handler wrappers
- **No SQS** — Router triggers Orchestrator via async Lambda invocation (abstracted as `trigger_scale_up` callable passed to core logic). Deduplication via DynamoDB conditional writes.

## MVP Definition

**MVP = Phases 0-4**: Working E2E on LocalStack via `curl` or OpenAI Python client. API key auth, auto scale-up/down, OpenAI-compatible inference (mocked).

## Phases

### Phase 0: Scaffolding

**Files:**
- `template.yaml` — SAM template (parameters, DynamoDB tables, EventBridge, API Gateway)
- `samconfig.toml` — deploy config
- `control-plane/core/__init__.py`, `interfaces.py` — Protocol definitions
- `control-plane/backends/aws/__init__.py`, `state.py`, `compute.py` (stub), `handlers.py` (stub)
- `control-plane/backends/mock/__init__.py`, `compute.py`, `state.py`
- `control-plane/shared/__init__.py`, `config.py`
- `control-plane/pyproject.toml` — runtime deps + dev extras (`pytest`, `testcontainers[localstack]`)
- `tests/unit/conftest.py` — fixtures using mock backends
- `tests/e2e/conftest.py` — testcontainers LocalStack + mock vLLM
- `tests/e2e/mock_vllm.py` — mock vLLM HTTP server (background thread)
- `.gitignore`, `Makefile`

### Phase 1: Orchestrator

**Files:**
- `control-plane/core/orchestrator.py`:
  - `scale_up(model_name, state, compute)` — idempotent: check DynamoDB for existing starting/ready instance (conditional write), launch via compute backend, poll health, register
  - `scale_down(state, compute)` — find idle instances past timeout, terminate
  - `poll_health(ip, port, timeout, interval)` — generic HTTP health check
- `control-plane/backends/aws/compute.py` — EC2 `launch()` / `terminate()`
- `control-plane/backends/aws/handlers.py` — `orchestrator_handler(event, context)`: dispatch async invoke (scale-up) vs EventBridge (scale-down)
- `ami/imagebuilder-template.yaml`, `ami/imagebuilder.sh` — GPU AMI pipeline/bootstrap via EC2 Image Builder
- `tests/unit/test_orchestrator.py`

### Phase 2: Streaming Router

**Files:**
- `control-plane/backends/aws/streaming_router.js`:
  - validates `zllm-` API keys
  - finds a ready instance or returns 503 + triggers async scale-up
  - streams upstream llama-server response chunks through a Lambda Function URL
- `template.yaml` — exposes `StreamingApiUrl` with `InvokeMode: RESPONSE_STREAM`

### Phase 3: Auth (API Keys)

**Files:**
- `control-plane/core/auth.py` — `validate_api_key(token, state) -> (bool, email)`
- `control-plane/core/keys.py` — `create_key(email, name, state)`, `list_keys(email, state)`, `delete_key(hash, email, state)`
- `control-plane/backends/aws/handlers.py` — add `authorizer_handler` (API Gateway v2 format), `keys_handler`
- `scripts/create_api_key.py` — CLI seed script
- `tests/unit/test_auth.py`, `tests/unit/test_keys.py`

### Phase 4: Cluster State + E2E Test

**Files:**
- `control-plane/core/cluster.py` — `get_cluster_state(state)`, `manual_scale(model, action, state, trigger_scale_up)`
- `control-plane/backends/aws/handlers.py` — add `cluster_handler`
- `tests/unit/test_cluster.py`
- `tests/e2e/test_full_lifecycle.py` — full cold-start → inference → scale-down cycle
- `tests/e2e/test_auth.py` — rejected without key, accepted with key

**Run:** `uv run --project control_plane --no-sync pytest tests/unit/ && uv run --project control_plane --no-sync pytest tests/e2e/`

### Phase 5: Google OAuth (post-MVP)
### Phase 6: Web UI (post-MVP)
### Phase 7: Polish (post-MVP)

## Testing Strategy

### Unit Tests (mock backends, no Docker)
- Core logic tested against `InMemoryStateStore` + `MockComputeBackend`
- Install deps: `uv sync --project control_plane --extra dev`
- Run: `uv run --project control_plane --no-sync pytest tests/unit/`

### E2E Tests (testcontainers)
- `testcontainers[localstack]` spins up LocalStack from pytest
- Mock vLLM runs as a background thread (`http.server`)
- Tests call the AWS Lambda handlers directly (pointed at LocalStack DynamoDB) and verify full lifecycle
- Install deps: `uv sync --project control_plane --extra dev`
- Run: `uv run --project control_plane --no-sync pytest tests/e2e/` (requires Docker)
