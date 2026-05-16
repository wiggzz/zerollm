# ZeroLLM — TODO / Tech Debt

Items are loosely grouped by area. None are blocking, but they're worth addressing
when there's spare time.

When investigations uncover non-blocking issues or cleanup work, add them here
instead of leaving them only in chat history or local notes.

---

## Bugs / Correctness

- **API Gateway first request can exceed common client timeouts**. On 2026-05-13,
  an authenticated `/api/cluster` request timed out at 10 seconds because the
  Python authorizer cold start took about 6.3s and the cluster handler took about
  6.1s. A retry returned `200` immediately. Consider reducing import/cold-start
  overhead, increasing Lambda memory, or using provisioned concurrency for the
  control-plane Lambdas if interactive clients depend on short timeouts.

- **Qwen3.6 context currently disables llama.cpp speculative decoding**. Explicit
  `ngram-mod` flags start successfully, but startup logs say
  `common_speculative_is_compat: the target context does not support partial sequence removal`
  followed by `speculative decoding not supported by this context`. Figure out whether
  this is caused by Qwen3.6's hybrid/recurrent architecture, prompt cache, 262k context,
  or the current llama.cpp build before re-enabling speculative flags. Upstream
  tracking: ggml-org/llama.cpp#20039. Follow-up research on 2026-05-02 found the
  deployed logs were running `build_info: b8757-a29e4c0b7`, after upstream
  speculative checkpointing merged in ggml-org/llama.cpp#19493. Retest with the
  checkpoint path explicitly enabled, e.g. `--spec-type ngram-mod --draft-max 48`
  plus the current checkpoint flag syntax, and record acceptance rate / tokens per
  second before making it a default.

- **`seed_models.py` without `--upload` or `--use-s3` can remove `s3_key` from
  deployed rows**. Running the normal seed command against a stack that expects
  S3-backed model files rewrites model configs without `s3_key`, causing new GPU
  instances to look for prebaked `/opt/models/*.gguf` files instead of downloading
  from S3. Make S3-backed seeding the default when `ModelsBucketName` exists, or
  preserve an existing `s3_key` unless the operator explicitly clears it.

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

- **`compute.py` user_data embeds `VLLM_ARGS` with double-quotes** (line ~199):
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

- **Basic CI is missing**. Add GitHub Actions that run unit tests and the lightweight
  e2e/LocalStack test suite on each commit or pull request, then add a build badge to
  README once the workflow is stable.

- **Real AWS bootstrap smoke test is missing**. Once `zlmctl` exists, CI should be
  able to run the actual onboarding path (`zlmctl up aws`), call the deployed
  streaming endpoint with an OpenAI-compatible chat/completions or responses request,
  retry through the expected first cold start, validate real assistant output, collect
  `zlmctl status` / `zlmctl logs` on failure, and always run `zlmctl destroy --yes`
  for cleanup. Start as a manually triggered or scheduled workflow before making it
  pull-request blocking.

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

- **Streaming Function URL lacks an automated integration test**. The Node.js handler
  is syntax-checked locally, but there is no LocalStack/e2e coverage for
  `InvokeMode: RESPONSE_STREAM` behavior.

---

## Design / Encapsulation

- **`manual_scale down` cannot terminate real instances by design** because the
  cluster API only receives `state` and `trigger_scale_up`, not `compute`. Fixing the
  bug above requires changing the core signature and the AWS handler dependency wiring.

- **`trigger_scale_up` is a bare callable passed through the cluster API**
  (`handlers.py`, `cluster.py`, tests). It's effectively a thin wrapper around a
  Lambda async invoke.
  This could be a named interface (`ScaleUpTrigger` protocol) to make the boundary
  explicit and testable. Not critical but the current design makes it easy to pass
  the wrong thing silently.

- **`handlers.py` builds `EC2ComputeBackend` inline on every invocation** via
  `_get_compute_backend()`, unlike `_get_state_store()` which is cached. The compute
  backend is stateless so this is harmless, but it's inconsistent and creates a new
  boto3 client on every handler call. Should be cached the same way the state store is.

- **`instance-logs.sh` hardcodes port 8000** for the health check curl. It should
  use the same port constant/convention as the rest of the codebase.

- **`DynamoDBStateStore` exposes `._models` (the raw Table object)** and
  `test_localstack_handlers.py` uses it directly (`state._models.put_item(...)`).
  That leaks the DynamoDB implementation detail into tests. `DynamoDBStateStore`
  should expose a `put_model_config` method matching the `InMemoryStateStore`, and
  the interface should include it.

---

## Naming / Consistency

- **Document migration path for pre-rename Diogenes AWS resources**. The project
  defaults now use ZeroLLM names, but older deployments may still have `diogenes-*`
  stacks, buckets, DynamoDB tables, log groups, API keys, AMIs, and local
  `.diogenes/` deploy defaults. Add clear guidance for keeping old resources via
  explicit env vars versus recreating them under the new defaults.

- **Audit and normalize remaining runtime naming drift**. The project now runs
  `llama-server`, but older `vllm` names still appear across env vars, service names,
  logs, mocks, docs, and tests. After the concrete renames below, do a final repo-wide
  audit for `vllm` / `VLLM` references and either rename them or document why they
  intentionally remain.

- **`vllm_args` field name throughout the data model** (DynamoDB, seed_models.py,
  compute.py) is now a misnomer — those args are passed to `llama-server`, not vLLM.
  Should rename to `server_args`. Requires a seed re-run to update DynamoDB.

- **Service name in systemd is still `vllm`** (the unit file is `vllm.service`, logs
  go to `/var/log/vllm.log`). Works fine, but if someone reads the instance logs or
  does `systemctl status` it looks like vLLM is running. Could rename to `llm-server`
  across the AMI template, scripts, and documentation.

- **Instance env var is still `VLLM_ARGS`**. The EC2 runtime uses
  `/etc/zerollm-model.env` with `VLLM_ARGS=...`, even though those arguments are
  passed to `llama-server` from llama.cpp. Rename the instance env var together with
  the systemd service cleanup, while preserving compatibility for already-built AMIs
  during migration.

- **`mock_vllm.py` and `MockVLLMServer`** — the server/file name says vLLM but it's
  just a generic mock HTTP server. Could rename to `mock_llm_server.py` /
  `MockLLMServer` for consistency.

---

## UX / Client Experience

- **Set up release-please for `zlmctl` releases and installer artifacts**. The Rust
  CLI should have automated version bumps, changelogs, tags, GitHub Releases, and
  published binaries/install scripts. Each release should also publish matching AWS
  deploy artifacts (templates, Lambda packages, model manifest, checksums) so
  `zlmctl up aws --version <x>` deploys a coherent artifact set.

- **Make first deploy possible without cloning the repo**. SAM-template-only deploy
  likely is not enough now because bootstrap needs CLI orchestration: discover or
  create AWS network defaults, build/select the GPU AMI, deploy/update stacks, seed
  model configs, create API keys, upload/sync model files, and print a working
  OpenAI-compatible endpoint. Design a small `zlmctl` CLI around this flow, with a
  provider abstraction so AWS comes first and GCP/Azure can be added later.

- **README deploy flow still requires manual follow-up steps**. `make deploy` creates
  infrastructure but does not seed model configs or create an API key. A first-time
  user can deploy successfully and still get empty `/v1/models` plus authorizer
  failures. Consider a guided bootstrap target that runs deploy, seeds models, prints
  `ApiUrl`, and creates or imports an initial API key.

- **Cold-start 503 message is a flat string** in the streaming router. The llama-server
  `/health` endpoint returns `{"status": "loading model"}` while loading and
  `{"status": "ok"}` when ready. Better approach:
  1. In `check_health` (`orchestrator.py`), when an instance isn't ready, store the
     raw `/health` response body as `status_message` on the DynamoDB instance record.
  2. In `streaming_router.js`, read `starting_instances[0].get("status_message")`
     and include it in the 503 body, falling back to a generic message.
  This gives the client the actual llama-server phase ("loading model", "error", etc.)
  without log parsing. The `Retry-After` header can also be tuned per-phase.

- **Needs vetting: `--parallel 1` makes concurrent requests queue behind one long
  generation**. This was chosen to fit 27B on a single A10G, but live testing showed
  queued Router invocations timing out while llama-server processed one slot. Evaluate
  whether lower context, a smaller quant, or a larger instance could support
  `--parallel 2` without OOM.

---

## Observability / Operations

- **Document and enforce the `zlmctl` AWS trust boundary**. Before `zlmctl up aws`
  mutates an account, it should print a concrete plan of resources it will create,
  the account/region/stack it will target, the fact that GPU EC2 instances may launch
  on inference, and what `zlmctl destroy` will remove or preserve. Publish a minimal
  IAM policy and a CloudFormation template for a dedicated deploy role, support
  normal AWS credential-chain/profile usage for deploying through that role, tag all
  owned resources, avoid reading unrelated account resources, and support dry-run/plan
  output plus operator-supplied permissions boundaries where possible.

- **Generate deploy-role IAM/CloudFormation templates instead of publishing
  account-specific docs**. The operator should be able to run a parametrized command
  such as `zlmctl aws permissions --region us-east-2 --stack-name zerollm` to print
  the deploy identity policy and optional CloudFormation execution-role template for
  their account. Keep static docs conceptual so examples don't drift or encode one
  developer account's ARNs.

- **Capture inference performance metrics outside raw logs**. Today throughput is
  only available by scraping llama-server timing lines from CloudWatch Logs, which is
  awkward and easy to skew. Add structured per-request metrics for model, instance
  type, prompt tokens/sec, generation tokens/sec, generated token count, total latency,
  cache-hit context, and speculative decoding stats such as draft acceptance rate.
  Start with CloudWatch Embedded Metric Format from the router or a small log parser,
  then consider a DynamoDB/S3 benchmark table for controlled experiment runs.

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

- **Needs vetting: deploy-time model sync should surface a concise summary**.
  CodeBuild worked, but validation required manually reading CloudWatch logs. Consider
  having `deploy.sh` optionally wait for the build and print uploaded/skipped/pruned
  model rows, S3 object sizes, and the CodeBuild log link.

- **Needs vetting: CodeBuild model sync dependency install time**. The model sync job
  installs Python dependencies on every run. This is not on the inference hot path, but
  a CodeBuild cache or custom image would make redeploys cleaner and faster. A fresh
  ZeroLLM deploy on 2026-05-05 was killed with exit 137 on `BUILD_GENERAL1_MEDIUM`
  while downloading Hugging Face model files, so the project now uses
  `BUILD_GENERAL1_LARGE`; monitor whether that is sufficient and whether the extra
  cost is worth avoiding a custom sync image.

---

## Cold-Start Candidates Requiring Vetting

- **Evaluate replacing the custom GPU AMI with a public GPU base AMI**. Since
  models now come from S3 at instance boot, the custom Image Builder pipeline mainly
  bakes Docker, NVIDIA container runtime, CloudWatch agent, the llama.cpp CUDA image,
  systemd wiring, and boot-service cleanup. Benchmark a public AWS GPU AMI path
  (ECS GPU-optimized AL2023 or Deep Learning Base GPU Ubuntu 24.04) with user-data
  setup plus optional `docker pull`, and compare total cold-start time, reliability,
  image freshness, and onboarding complexity against the dedicated AMI.

- **Reduce health-check readiness lag**. EventBridge currently polls once per minute,
  so DynamoDB `ready` can lag actual llama-server readiness by up to about 60 seconds.
  Evaluate a 15-20 second schedule or a short burst poll after launch.

- **Instance callback on readiness**. Instead of only polling, cloud-init or the
  systemd unit could call back when llama-server is ready. Needs a secure mechanism
  and careful failure handling; polling is simpler and safer today.

- **Stop/start policy for large models**. S3 download of the Qwen 3.6 27B GGUF took
  about 106 seconds, and total cold start to `ready` was about 5-6 minutes. Stopping
  instead of terminating could preserve EBS contents and avoid the S3 download on
  reuse. Needs cost and stale-volume lifecycle evaluation.

- **Keep-warm policy for selected models**. For active testing or high-value models,
  skipping scale-down entirely may be the best user experience. Needs explicit
  per-model policy and cost controls.

- **Expose cold-start phase data to clients**. Store timestamps such as
  `cloud_init_start`, `model_download_start`, `model_download_done`,
  `llama_service_start`, and health status in DynamoDB so 503 responses can include
  accurate progress instead of a flat retry message.

- **Needs vetting: increase Qwen 3.6 context window beyond 32k**. Startup logs at
  `--ctx-size 32768` showed about 18,038 MiB projected GPU usage on A10G, with about
  3,665 MiB projected free. KV cache was about 2,048 MiB at 32k, so 64k should add
  roughly another 2 GiB and may fit on the current `g5.2xlarge`; 96k/128k likely need
  either a smaller quant or more VRAM. First experiment: update only the model config
  to `--ctx-size 65536`, cold-start a fresh instance, and verify llama-server fit/load
  logs plus real prompt behavior.

- **Needs vetting: more-VRAM instance options within a 32 vCPU budget**. Current
  `g5.2xlarge` has one A10G-class GPU with 24 GB GPU memory and 8 vCPUs. AWS docs list
  `g6e.xlarge`, `g6e.2xlarge`, `g6e.4xlarge`, and `g6e.8xlarge` as single L40S-class
  GPU instances with 44 GiB accelerator memory and 4/8/16/32 vCPUs respectively.
  These are the main candidates for larger context while staying inside 32 vCPUs.
  Need to verify regional availability, quota, AMI driver compatibility, capacity,
  price, and llama.cpp CUDA behavior before switching defaults.

---

## Simplifications

- **README, DESIGN, and PLAN describe different generations of the system**.
  README is closest to current behavior, but DESIGN/PLAN still mention SQS,
  synchronous health polling during scale-up, vLLM-first model args, Google OAuth,
  and a web UI as if they exist. Either mark DESIGN/PLAN as historical or refresh
  them so new contributors don't implement against stale architecture.

- **`deploy.sh` persists sensitive deploy defaults in plaintext**. `VLLM_API_KEY`
  is sensitive and ends up in a plaintext `.zerollm/` file alongside
  `HF_TOKEN_SECRET_ARN`. Consider using AWS SSM Parameter Store (SecureString) or
  Secrets Manager for the API key instead of a local file, and just look it up at
  deploy time.

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
