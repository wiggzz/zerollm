# scale-zero-llm CLI Onboarding

## Goal

Make scale-zero-llm deployable without cloning the repository.

The first-run experience should feel like installing a normal infrastructure CLI,
running one command, and receiving a working OpenAI-compatible endpoint. The user
should not need to understand SAM, CloudFormation packaging, Image Builder,
DynamoDB seed data, model sync jobs, or API key table internals before trying the
system.

Target first run:

```bash
szlctl up aws
```

Expected output should include:

- deployed stack name
- streaming base URL
- initial API key
- configured model list
- a copy-pasteable test request
- the cleanup command

## CLI Stack

`szlctl` should be a Rust binary.

Reasons:

- single-file distribution through GitHub Releases, Homebrew, package managers, or
  a small install script
- no Python, `uv`, virtualenv, or local repo checkout required for operators
- fast startup and predictable behavior on developer laptops and CloudShell
- a clean long-term home for AWS, GCP, and Azure provider modules

The deployed control plane can remain Python. The CLI should deploy released
artifacts rather than requiring users to build Lambda packages locally.

## Minimum V0 Command Set

V0 should expose only the commands required for first deploy, inspection, debugging,
and teardown:

```bash
szlctl up aws
szlctl status
szlctl logs
szlctl destroy
```

Avoid exposing separate V0 commands for AMI management, SAM packaging, model
seeding, API key creation, and model sync unless they become necessary for real
users. Those are implementation phases inside `up`.

## `szlctl up aws`

Deploy or update a working AWS stack.

This replaces the current manual sequence:

```bash
make deploy
make seed-models
make create-api-key EMAIL=you@example.com
```

Responsibilities:

- detect AWS account and region
- prompt for missing required inputs, such as owner email
- run preflight checks for credentials, region, quotas, required services, and GPU
  instance availability
- select or build the GPU AMI
- discover or create network defaults
- deploy CloudFormation from released artifacts
- seed default model configs
- start or configure model file sync
- create an initial API key
- print the streaming endpoint, key, models, and test request

Example:

```bash
szlctl up aws --region us-east-2 --email you@example.com
```

Non-interactive mode should be possible for CI and repeatable installs:

```bash
szlctl up aws \
  --region us-east-2 \
  --email you@example.com \
  --stack scale-zero-llm-dev \
  --yes
```

## `szlctl status`

Show whether the deployment is usable right now.

This replaces and expands the current `make status` path. It should combine stack
outputs, model rows, and instance state into one view.

Example output:

```text
Stack: scale-zero-llm-dev
Region: us-east-2
Streaming URL: https://example.lambda-url.us-east-2.on.aws/

Models:
  Qwen/Qwen3.6-27B    configured
  Qwen/Qwen3.5-4B     configured

Instances:
  Qwen/Qwen3.6-27B    ready      i-0123456789abcdef0
  Qwen/Qwen3.5-4B     cold
```

## `szlctl logs`

Show useful operational logs without requiring users to know CloudWatch, SSM, EC2
instance IDs, or systemd unit names.

This replaces the current `make logs` path.

Examples:

```bash
szlctl logs
szlctl logs --model Qwen3.6
szlctl logs --follow
```

V0 should prioritize:

- current GPU instance state
- startup and health-check failures
- llama-server logs
- model download/sync failures
- orchestrator scale-up and scale-down decisions

## `szlctl destroy`

Delete the deployment and stop spend.

This is part of onboarding, not an advanced operation. A first-time user needs an
obvious way to clean up resources.

Examples:

```bash
szlctl destroy
szlctl destroy --yes
```

Responsibilities:

- terminate active GPU instances before stack deletion when needed
- delete the CloudFormation stack
- clearly state whether model artifacts are preserved or deleted
- support a confirmation prompt by default

## Hidden V0 Phases

These should be internal phases of `szlctl up aws`, not top-level commands at
first:

- `doctor` / preflight
- build or select AMI
- deploy/update infrastructure
- seed model configs
- create initial API key
- upload or sync model files
- print client configuration

They can become separate commands later if operators need them.

## Release Artifact Model

The CLI should deploy published artifacts, not local source trees.

Release artifacts should include:

- CloudFormation templates
- Lambda deployment packages
- streaming router package
- model manifest
- checksums or signatures
- `szlctl` binaries for supported platforms

This avoids requiring users to install SAM, `uv`, Python dependencies, or clone the
repo just to deploy.

## CI Bootstrap Smoke Test

The repository should eventually run a real AWS bootstrap test that proves the
published onboarding path works end to end.

The test should exercise the same path a new user runs:

```bash
szlctl up aws --yes --region us-east-2 --email ci@example.com
```

Then it should call the deployed OpenAI-compatible endpoint and validate that a real
model responds:

```bash
curl "$STREAMING_URL/v1/chat/completions" \
  -H "Authorization: Bearer $API_KEY" \
  -H "Content-Type: application/json" \
  --json '{
    "model": "Qwen/Qwen3.5-4B",
    "messages": [{"role": "user", "content": "Answer with exactly: bootstrap-ok"}],
    "max_tokens": 32,
    "temperature": 0
  }'
```

Because the first request may cold-start a GPU instance, the test harness should
handle the expected warmup loop:

- send an inference request
- accept `503` responses that include `Retry-After`
- sleep and retry until a configurable deadline
- fail with useful `szlctl status` and `szlctl logs` output on timeout
- validate the final response has non-empty assistant text, and preferably contains
  the requested sentinel string

The test should always run cleanup:

```bash
szlctl destroy --yes
```

V0 can run this workflow manually or on a scheduled CI job. Pull-request gating
should wait until cost controls, cleanup reliability, account isolation, and runtime
duration are proven.

## Provider Shape

Keep user-facing commands stable while providers implement the cloud-specific work.

Suggested provider phases:

- `preflight`
- `plan`
- `deploy`
- `seed`
- `status`
- `logs`
- `destroy`

AWS should be first. GCP and Azure can come later behind the same top-level command
shape:

```bash
szlctl up gcp
szlctl up azure
```

## Explicit Non-Goals For V0

- no full Terraform/Pulumi authoring interface
- no separate advanced AMI command surface
- no local Lambda build requirement
- no web UI dependency for first deploy
- no mandatory config file before first deploy

A config file such as `scale-zero-llm.toml` can be generated after first deploy for
repeatability, but it should not be required before a user can try the system.
