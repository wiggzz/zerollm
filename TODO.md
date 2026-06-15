# ZeroLLM TODO

Live follow-ups only. Completed work and historical investigation notes belong in git history, issues, or PRs.

## Correctness

- Verify synced GGUF artifacts before smoke waits on cold start. Record expected size/checksum in `models.json` and fail model sync if a downloaded artifact is incompatible with the deployed `llama-server` build.
- Preserve S3-backed model rows when running `seed_models.py` without upload options. A normal seed should not erase an existing `s3_key` unless explicitly requested.
- Paginate `DynamoDBStateStore.list_instances()` scan paths. Status-only scans can silently truncate after 1 MB.
- Escape model runtime args in EC2 user data. `VLLM_ARGS="{vllm_args}"` breaks if trusted seed data ever contains double quotes.
- Write deploy defaults with shell-safe quoting, or stop sourcing the defaults file as shell.

## Client Behavior

- Replace cold-start 503s with wait-until-ready streaming behavior. Keep OpenAI-compatible streams valid for normal clients while exposing richer progress events for clients that opt in.
- Decide how clients should cancel backend generation. Lambda Function URL response streaming does not reliably stop the upstream request when a caller disconnects.
- Evaluate the `--parallel 1` queueing tradeoff. Long generations can block following requests; any change must fit the target model and GPU memory.
- Reduce control-plane cold-start latency for interactive API routes if it keeps exceeding common 10s client timeouts.

## Diagnostics

- Investigate "Power key pressed" in journal on warm-started GPU instances. EC2 stop/start should trigger a clean ACPI shutdown, not a power key event. Could indicate an unexpected code path in the orchestrator or cloud-init.

## Tooling

- Auto-bump AMI ImageVersion on template changes. CloudFormation fails with `AlreadyExists` if the ImageBuilder recipe version isn't incremented when the template changes. Options: auto-bump in deploy.sh, use git hash as version in CI, or add a PR check that fails if template changed but version didn't bump.

## Operations

- Move GPU instance ingress off public `0.0.0.0/0:8000`, preferably by putting the router in the VPC or restricting ingress to explicit CIDRs/security groups.
- Add alarms or metrics for startup failures, health-check timeouts, and repeated cold-start failure loops.
- Capture structured inference metrics: model, instance type, prompt/generation tokens per second, generated tokens, latency, and MTP acceptance stats.
- Make deploy/model-sync output operator-friendly: uploaded/skipped/pruned model rows, S3 object sizes, CodeBuild run URL, and concise failure details.
- Add a cache or custom image for model sync if dependency install time keeps making deploys noisy.
- Update GitHub workflow actions for the Node.js 20 deprecation before GitHub changes runner defaults.

## Product / Packaging

- Make first deploy possible without cloning the repo. A `zlmctl` bootstrap should discover/create AWS prerequisites, deploy, sync models, create/import an API key, and print the working endpoint.
- Document and enforce the `zlmctl` AWS trust boundary: target account/region, resources created, GPU cost implications, dry-run output, destroy behavior, and least-privilege deploy role.
- Generate deploy-role IAM/CloudFormation templates instead of keeping account-specific instructions in docs.
- Set up release automation for `zlmctl` binaries, changelog, and matching deploy artifacts.

## Naming Cleanup

- Rename `vllm_args` to `server_args` across manifest, DynamoDB seed rows, and runtime code.
- Rename the EC2 `vllm.service`, `/var/log/vllm.log`, `VLLM_ARGS`, and related local names to llama-server-neutral names while preserving migration compatibility.
- Rename test helpers such as `mock_vllm.py` / `MockVLLMServer` to generic LLM server names.
- Remove or implement the stale Google OAuth/JWT path. Programmatic API keys are real; Google auth is still scaffolding.

## Experiments

- Benchmark Qwen3.6 MTP on the current default instance: load time, prompt tokens/sec, generated tokens/sec, MTP acceptance rate, and useful context ceiling.
- Compare the custom GPU AMI against a public GPU base AMI plus user-data setup now that model weights sync from S3.
- Evaluate shorter readiness polling or an instance readiness callback to reduce the current EventBridge lag.
