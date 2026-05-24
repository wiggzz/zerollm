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

## Deploy Dev Roles

Run with privileged local AWS credentials:

    cd infrastructure
    ./deploy.sh dev

The deploy script creates the state bucket if it is missing, enables versioning,
blocks public access, enables AES-256 server-side encryption, and runs
`terraform init -migrate-state -force-copy` before applying.

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
