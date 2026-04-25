# Diogenes — TODO / Tech Debt

Items are loosely grouped by area. None are blocking, but they're worth addressing
when there's spare time.

---

## Bugs / Correctness

- **`proxy_request` assumes every non-SSE upstream response is JSON** (`router.py:29`).
  If `llama-server` returns an HTML/text error page or an empty body, `response.json()`
  raises and the router converts the upstream response into a generic 502. Preserve
  the upstream status and return a safe text/error payload when JSON parsing fails.

- **`manual_scale down` skips actual EC2 termination** (`cluster.py:75`).
  It calls `state.update_instance(status="terminated")` directly without calling
  `compute.terminate()`. The instance is marked gone in DynamoDB but keeps running
  on EC2 and billing until the next scale_down sweep picks it up (which it won't,
  because the status is already "terminated"). Fix: pass `compute` into `manual_scale`
  and call it, matching how `scale_down` works.

- **DynamoDB `list_instances` with status-only filter does a full table scan** (`state.py:48`).
  The `model-status-index` GSI only supports hash+range queries on (model, status).
  A status-only lookup (e.g. `check_health` fetching all "starting" instances) falls
  through to `scan()` with a FilterExpression. This is fine at small scale but won't
  paginate — if there are ever >1 MB of instance records, results will be silently
  truncated. Fix: add a GSI on `status` alone, or add pagination (`LastEvaluatedKey`
  loop) to the scan path.

- **`e2e/test_full_lifecycle.py` calls `scale_up` and asserts `status == "ready"`**
  (`test_full_lifecycle.py:24`). After the EventBridge polling refactor, `scale_up`
  returns `status="starting"` immediately — it no longer waits for health. This test
  is broken (it monkeypatches `VLLM_PORT` but the health check loop is gone). The
  test needs to be rewritten to call `check_health` after `scale_up`, matching the
  new architecture.

- **`compute.py` user_data embeds `VLLM_ARGS` with double-quotes** (line ~103):
  `VLLM_ARGS="{vllm_args}"`. If `vllm_args` contains a double-quote character this
  breaks the env file. The value is written unescaped into the heredoc.
  Low risk in practice since vllm_args comes from trusted seed data, but worth
  hardening.

- **`save_pinned_defaults` writes shell-unquoted values** (`deploy.sh`).
  `GPU_SUBNET_ID` is currently a comma-separated list and `VLLM_API_KEY` is hex, so
  this happens to work. If future values contain spaces, quotes, or shell metacharacters,
  sourcing the defaults file will break or execute unintended syntax. Write values
  with `printf '%q'` or use a simple dotenv parser that does not execute the file.

---

## Missing Tests

- **`proxy_request` has no test for non-JSON error responses**. Add coverage for
  upstream 500/502 text bodies so JSON decoding failures don't mask the real status.

- **`check_health` is not tested when an instance has no `provider_instance_id`**
  (placeholder-only record before EC2 call returns). The timeout path skips the
  `compute.terminate()` call in this case — no test verifies that.

- **`manual_scale down` has no test for the EC2 termination gap** described above.

- **`check_health` EventBridge handler path in `handlers.py`** has no unit test
  — only the core `check_health()` function is covered. Should add a handler-level
  test that exercises the `"action": "check_health"` dispatch and JSON serialization.

- **`compute.py` `_build_user_data`** has no tests. It's pure string construction
  and easy to regress (e.g. variable names, heredoc quoting). A simple unit test
  that checks the output contains `MODEL_NAME`, `VLLM_ARGS`, `llama-server` etc.
  would catch regressions.

- **`cluster.py` `get_cluster_state`** doesn't test the "warming" (starting) state
  path — only ready and cold are tested.

- **E2E tests don't cover streaming (SSE)**. The unit test for SSE exists, but
  there's no e2e path exercising it through the full handler stack.

---

## Design / Encapsulation

- **`manual_scale down` cannot terminate real instances by design** because the
  cluster API only receives `state` and `trigger_scale_up`, not `compute`. Fixing the
  bug above requires changing the core signature and the AWS handler dependency wiring.

- **`trigger_scale_up` is a bare callable passed everywhere** (`router.py`,
  `cluster.py`, tests). It's effectively a thin wrapper around a Lambda async invoke.
  This could be a named interface (`ScaleUpTrigger` protocol) to make the boundary
  explicit and testable. Not critical but the current design makes it easy to pass
  the wrong thing silently.

- **`handlers.py` builds `EC2ComputeBackend` inline on every invocation** via
  `_get_compute_backend()`, unlike `_get_state_store()` which is cached. The compute
  backend is stateless so this is harmless, but it's inconsistent and creates a new
  boto3 client on every handler call. Should be cached the same way the state store is.

- **Port constant is defined in three places**: `orchestrator.py` (`SERVER_PORT = 8000`,
  `VLLM_PORT = SERVER_PORT`), `router.py` (`VLLM_PORT = 8000`), `config.py`
  (`VLLM_PORT = 8000`). The `config.py` one appears unused. Pick one place (probably
  `config.py`) and import from there.

- **`cluster.py` `manual_scale`** validates model existence before acting but
  `router.py` `handle_inference` does not — it calls `trigger_scale_up` for any
  unknown model name, which will result in a `ValueError` inside the orchestrator
  Lambda that's swallowed silently. The router should check that the model exists
  before triggering and return a 404 instead.

- **`instance-logs.sh` hardcodes port 8000** for the health check curl. It should
  use the same port constant/convention as the rest of the codebase.

- **`DynamoDBStateStore` exposes `._models` (the raw Table object)** and
  `test_localstack_handlers.py` uses it directly (`state._models.put_item(...)`).
  That leaks the DynamoDB implementation detail into tests. `DynamoDBStateStore`
  should expose a `put_model_config` method matching the `InMemoryStateStore`, and
  the interface should include it.

---

## Naming / Consistency

- **`vllm_args` field name throughout the data model** (DynamoDB, seed_models.py,
  compute.py) is now a misnomer — those args are passed to `llama-server`, not vLLM.
  Should rename to `server_args`. Requires a seed re-run to update DynamoDB.

- **Service name in systemd is still `vllm`** (the unit file is `vllm.service`, logs
  go to `/var/log/vllm.log`). Works fine, but if someone reads the instance logs or
  does `systemctl status` it looks like vLLM is running. Could rename to `llm-server`
  across the AMI template, scripts, and documentation.

- **`mock_vllm.py` and `MockVLLMServer`** — the server/file name says vLLM but it's
  just a generic mock HTTP server. Could rename to `mock_llm_server.py` /
  `MockLLMServer` for consistency.

---

## UX / Client Experience

- **README deploy flow still requires manual follow-up steps**. `make deploy` creates
  infrastructure but does not seed model configs or create an API key. A first-time
  user can deploy successfully and still get empty `/v1/models` plus authorizer
  failures. Consider a guided bootstrap target that runs deploy, seeds models, prints
  `ApiUrl`, and creates or imports an initial API key.

- **Cold-start 503 message is a flat string** (`router.py:59`). The llama-server
  `/health` endpoint returns `{"status": "loading model"}` while loading and
  `{"status": "ok"}` when ready. Better approach:
  1. In `check_health` (`orchestrator.py`), when an instance isn't ready, store the
     raw `/health` response body as `status_message` on the DynamoDB instance record.
  2. In `router.py`, read `starting_instances[0].get("status_message")` and include
     it in the 503 body, falling back to a generic message.
  This gives the client the actual llama-server phase ("loading model", "error", etc.)
  without log parsing. The `Retry-After` header can also be tuned per-phase.

---

## Observability / Operations

- **GPU inference port is exposed to the public internet** (`template.yaml`).
  `GpuSecurityGroup` allows `0.0.0.0/0` to port 8000. The shared `VLLM_API_KEY` helps,
  but public instance exposure is still a large operational footgun. Prefer putting
  the router Lambda in the VPC and restricting ingress to the Lambda security group,
  or at least make allowed CIDRs an explicit deploy parameter.

- **No CloudWatch alarm on instance startup failures**. If `check_health` terminates
  an instance after timeout, it logs an error but there's no metric or alarm. An
  operator won't know a model is stuck in a boot failure loop until a user reports 503s.

- **`make status` shows all instances including terminated ones** from DynamoDB.
  `cluster.py` filters terminated instances out of the API response, and
  `instance-logs.sh` filters them out before showing logs, but `cluster-status.sh`
  prints every DynamoDB row. Keep these views consistent and consider a periodic
  cleanup of old terminated records.

- **No way to see check_health invocation results** beyond CloudWatch Logs. A simple
  `/api/cluster` response field showing "last_health_check_at" would be useful.

---

## Simplifications

- **README, DESIGN, and PLAN describe different generations of the system**.
  README is closest to current behavior, but DESIGN/PLAN still mention SQS,
  synchronous health polling during scale-up, vLLM-first model args, Google OAuth,
  and a web UI as if they exist. Either mark DESIGN/PLAN as historical or refresh
  them so new contributors don't implement against stale architecture.

- **`normalize_model_name` is now a no-op** but is still imported and called in
  `orchestrator.py` and `router.py`. Delete the function and the call sites.

- **`poll_health` in `orchestrator.py` is dead code** — nothing calls it since the
  EventBridge refactor. Delete it.

- **`deploy.sh` `save_pinned_defaults` only persists `GPU_SUBNET_ID` and `VLLM_API_KEY`**
  but the file comment says it saves "pinned network defaults". The `VLLM_API_KEY` is
  sensitive and ends up in a plaintext `.diogenes/` file. Consider using AWS SSM
  Parameter Store (SecureString) for the API key instead of a local file, and just
  look it up at deploy time.

- **`handlers.py` has a `# TODO Phase 5: Google JWT validation`** comment in the
  authorizer. Either implement it or remove the comment — it's been there across
  multiple phases and the `GoogleClientId` parameter is wired throughout the template
  for something that isn't hooked up yet.

- **`scale_up` accepts a `vllm_api_key` parameter** it no longer uses (the key was
  needed for the old synchronous `poll_health` call). The parameter should be removed
  from `scale_up`'s signature; `check_health` takes `api_key` instead.

- **The `import time` inside `launch()` in `compute.py`** (line 82) is a local import
  for a stdlib module — no reason for it to be local. Move it to the top of the file.

- **`handlers.py` has `import os` duplicated** inside both `orchestrator_handler` and
  `_get_compute_backend` function bodies. These should either be module-level imports
  or at least deduplicated.
