# ZeroLLM CLI Onboarding

## Goal

Make ZeroLLM deployable without cloning the repository.

The first-run experience should feel like installing a normal infrastructure CLI,
running one command, and receiving a working OpenAI-compatible endpoint. The user
should not need to understand SAM, CloudFormation packaging, Image Builder,
DynamoDB seed data, model sync jobs, or API key table internals before trying the
system.

Target first run:

```bash
zlmctl up aws
```

Expected output should include:

- deployed stack name
- streaming base URL
- initial API key
- configured model list
- a copy-pasteable test request
- the cleanup command

## CLI Stack

`zlmctl` should be a Rust binary.

Prefer `zlmctl` over a shorter `zlm` binary for V0. `zlm` is terse, but it is
already used as a Python package name and as an R/statistics function name in
multiple packages. It does not appear to be a common system CLI, but `zlmctl` is
clearer, less collision-prone, and leaves room for a short alias later if users
actually want one.

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
zlmctl up aws
zlmctl status
zlmctl logs
zlmctl destroy
```

Avoid exposing separate V0 commands for AMI management, SAM packaging, model
seeding, API key creation, and model sync unless they become necessary for real
users. Those are implementation phases inside `up`.

## `zlmctl up aws`

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
zlmctl up aws --region us-east-2 --email you@example.com
```

Non-interactive mode should be possible for CI and repeatable installs:

```bash
zlmctl up aws \
  --region us-east-2 \
  --email you@example.com \
  --stack zerollm-dev \
  --yes
```

## `zlmctl status`

Show whether the deployment is usable right now.

This replaces and expands the current `make status` path. It should combine stack
outputs, model rows, and instance state into one view.

Example output:

```text
Stack: zerollm-dev
Region: us-east-2
Streaming URL: https://example.lambda-url.us-east-2.on.aws/

Models:
  Qwen/Qwen3.6-27B    configured
  Qwen/Qwen3.5-4B     configured

Instances:
  Qwen/Qwen3.6-27B    ready      i-0123456789abcdef0
  Qwen/Qwen3.5-4B     cold
```

## `zlmctl logs`

Show useful operational logs without requiring users to know CloudWatch, SSM, EC2
instance IDs, or systemd unit names.

This replaces the current `make logs` path.

Examples:

```bash
zlmctl logs
zlmctl logs --model Qwen3.6
zlmctl logs --follow
```

V0 should prioritize:

- current GPU instance state
- startup and health-check failures
- llama-server logs
- model download/sync failures
- orchestrator scale-up and scale-down decisions

## `zlmctl destroy`

Delete the deployment and stop spend.

This is part of onboarding, not an advanced operation. A first-time user needs an
obvious way to clean up resources.

Examples:

```bash
zlmctl destroy
zlmctl destroy --yes
```

Responsibilities:

- terminate active GPU instances before stack deletion when needed
- delete the CloudFormation stack
- clearly state whether model artifacts are preserved or deleted
- support a confirmation prompt by default

## Hidden V0 Phases

These should be internal phases of `zlmctl up aws`, not top-level commands at
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
- `zlmctl` binaries for supported platforms

This avoids requiring users to install SAM, `uv`, Python dependencies, or clone the
repo just to deploy.

Releases should be automated with release-please so CLI versions, changelogs, tags,
and GitHub Release artifacts stay consistent. A release should publish the Rust
`zlmctl` binaries plus the deployable AWS artifacts for the same version. The CLI
should default to deploying artifacts from its own version unless the user explicitly
chooses another release:

```bash
zlmctl up aws --version latest
zlmctl up aws --version 0.3.1
```

This keeps support and rollback concrete: the binary, templates, Lambda packages,
model manifest, and checksums all come from one release.

## AWS Trust Model

The onboarding flow must be explicit about what `zlmctl` will do in the user's AWS
account before it asks for confirmation.

This matters because the CLI needs AWS credentials that can create infrastructure.
That can look like arbitrary account access unless the tool clearly states its plan,
its required permissions, and the resources it will own.

CloudFormation should remain the deployment engine. `zlmctl` should not become a
general-purpose IaC tool or imperatively assemble the production stack through
ad-hoc SDK calls. Its job is to select versioned artifacts, resolve parameters,
create a CloudFormation change set, wait for stack completion, and run the small
post-deploy steps that CloudFormation is not a good fit for, such as model seeding
and initial API key creation.

`zlmctl up aws` should print a plan before changing anything:

```text
Account: 123456789012
Region: us-east-2
Stack: zerollm-dev

zlmctl will create or update:
  - CloudFormation stacks for the control plane and GPU runtime AMI
  - Lambda functions and a Lambda Function URL
  - DynamoDB tables for models, instances, and API keys
  - IAM roles/policies scoped to the stack
  - S3 bucket for model artifacts
  - CodeBuild project for model sync
  - EC2 Image Builder pipeline and runtime AMI
  - EC2 security group for GPU inference
  - EventBridge health-check schedule
  - CloudWatch log groups

zlmctl may launch GPU EC2 instances when inference requests arrive.
zlmctl destroy --yes removes the stack-managed resources.
```

The CLI should also make these boundaries clear:

- it should not create users, access keys, organizations, or account-wide identity
  resources
- it should not inspect unrelated buckets, tables, instances, secrets, or logs
- it should tag all owned resources so they are auditable
- it should support `--dry-run` or `plan` output before apply
- it should document the minimum IAM policy needed for deployment
- it should provide a CloudFormation template for a dedicated deploy role that
  cautious users can inspect and create before running the CLI
- it should rely on the standard AWS credentials chain, so cautious users can point
  `zlmctl` at a profile that already assumes the deploy role:
  `zlmctl up aws --profile zerollm-deployer`
- it should support an operator-supplied permissions boundary where possible
- it should make preserved resources explicit, especially model S3 objects and AMIs

The CLI probably cannot avoid needing broad deploy-time permissions for the first
AWS version, but it can reduce concern by being transparent, deterministic, tagged,
and easy to tear down.

There should be two supported trust paths:

- convenience path: use the caller's current AWS credentials, print the plan, and
  deploy the versioned CloudFormation stack after confirmation
- cautious path: deploy or manually create a constrained `zerollm-deployer`
  IAM role from an auditable template, configure a normal AWS profile that assumes
  that role, then run `zlmctl` with `--profile`

An explicit `--role-arn` flag could be added later for CI or one-off automation, but
the default local trust model should let the AWS SDK credential chain handle role
assumption rather than asking users to trust the CLI to switch roles correctly.

## CI Bootstrap Smoke Test

The repository should eventually run a real AWS bootstrap test that proves the
published onboarding path works end to end.

The test should exercise the same path a new user runs:

```bash
zlmctl up aws --yes --region us-east-2 --email ci@example.com
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
- fail with useful `zlmctl status` and `zlmctl logs` output on timeout
- validate the final response has non-empty assistant text, and preferably contains
  the requested sentinel string

The test should always run cleanup:

```bash
zlmctl destroy --yes
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
zlmctl up gcp
zlmctl up azure
```

## Explicit Non-Goals For V0

- no full Terraform/Pulumi authoring interface
- no separate advanced AMI command surface
- no local Lambda build requirement
- no web UI dependency for first deploy
- no mandatory config file before first deploy

A config file such as `zerollm.toml` can be generated after first deploy for
repeatability, but it should not be required before a user can try the system.
