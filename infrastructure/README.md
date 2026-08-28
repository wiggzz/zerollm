# ZeroLLM Infrastructure

Terraform bootstrap for GitHub Actions AWS access.

This layer assumes the AWS account already has the GitHub Actions OIDC provider
for token.actions.githubusercontent.com, matching the Sportnumerics account setup.
It creates a GitHub-assumable deployment role and a CloudFormation execution role
used by sam deploy --role-arn.

Terraform state is stored in S3 at:

- bucket: `zerollm-terraform-state-265978616089-us-east-2`
- key: `infrastructure/dev/terraform.tfstate`

The backend uses Terraform's S3 lockfile support, so Terraform 1.10 or newer is
required. The checked-in `.terraform.lock.hcl` remains the provider dependency
lock file and should stay in git.

## Deploy Environment Roles

Run with privileged local AWS credentials:

    cd infrastructure
    ./deploy.sh dev
    ./deploy.sh prod

Each environment has a separate Terraform state key and creates a separate GitHub-assumable deployment role plus CloudFormation execution role. The deploy script creates the state bucket if it is missing, enables versioning, blocks public access, enables AES-256 server-side encryption, and runs `terraform init -migrate-state -force-copy` before applying.

Useful outputs:

- github_deploy_role_arn: set as the GitHub secret AWS_ROLE_TO_ASSUME
- cloudformation_execution_role_arn: pass as CFN_ROLE_ARN

The default dev trust policy allows:

- repo:wiggzz/zerollm:environment:dev
- repo:wiggzz/zerollm:pull_request

If a workflow does not use the dev environment, add the specific branch/ref
subject through allowed_github_subjects.

The deployment policy is intended for SAM deploys that use an existing AMI
pipeline image, for example AMI_BUILD_MODE=latest. Building or updating the
Image Builder pipeline needs a separate, broader bootstrap permission set.

## Production Promotion

`AWS Smoke` emits an artifact that binds its successful commit, exact AMI ID,
region, and SHA-256 of `models.json`. `Production Deploy` runs only for a
successful push-to-main smoke whose commit is still the current `main` head; it
validates that artifact before using the recorded AMI ID for `zerollm-prod`.
It never promotes a mutable “latest” AMI reference.

Before enabling promotion, create the protected GitHub `prod` environment, run
`./deploy.sh prod`, and set these **prod-environment** secrets from Terraform
outputs:

- `AWS_ROLE_TO_ASSUME` ← `github_deploy_role_arn`
- `CFN_ROLE_ARN` ← `cloudformation_execution_role_arn`
- `HF_TOKEN_SECRET_ARN` (optional)

Set the GitHub Actions variable `PRODUCTION_DEPLOY_ENABLED=true` only after
those secrets and the role trust are in place. Until then, production-promotion
jobs are intentionally skipped. The prod trust permits only
`repo:wiggzz/zerollm:environment:prod`; it does not inherit dev or pull-request
access.
