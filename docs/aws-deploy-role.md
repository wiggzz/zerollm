# AWS Deploy Role

ZeroLLM deploys should go through CloudFormation. The local deploy identity only
needs enough access to create/update the stack and pass one CloudFormation execution
role; the execution role performs the resource mutations described by the template.

## CloudFormation Execution Role

Create a role such as `ZeroLLMDevCloudFormationExecutionRole` with this trust policy:

```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Effect": "Allow",
      "Principal": { "Service": "cloudformation.amazonaws.com" },
      "Action": "sts:AssumeRole"
    }
  ]
}
```

For a dev stack, attaching `AdministratorAccess` to this execution role is the
lowest-friction option. That still keeps direct user credentials away from broad
service-admin permissions and forces stack changes through CloudFormation events.
Tighten this later once the template stabilizes.

## Deploy Identity Policy

Attach a policy like this to the deploy user or role, replacing account, region,
bucket, and role names as needed:

```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Sid": "ManageZeroLLMStack",
      "Effect": "Allow",
      "Action": [
        "cloudformation:CreateChangeSet",
        "cloudformation:CreateStack",
        "cloudformation:DeleteChangeSet",
        "cloudformation:DescribeChangeSet",
        "cloudformation:DescribeStackEvents",
        "cloudformation:DescribeStacks",
        "cloudformation:ExecuteChangeSet",
        "cloudformation:GetTemplate",
        "cloudformation:ListChangeSets",
        "cloudformation:UpdateStack"
      ],
      "Resource": "arn:aws:cloudformation:us-east-2:265978616089:stack/zerollm/*"
    },
    {
      "Sid": "PassCloudFormationExecutionRole",
      "Effect": "Allow",
      "Action": "iam:PassRole",
      "Resource": "arn:aws:iam::265978616089:role/ZeroLLMDevCloudFormationExecutionRole",
      "Condition": {
        "StringEquals": { "iam:PassedToService": "cloudformation.amazonaws.com" }
      }
    },
    {
      "Sid": "SamArtifactBucketAccess",
      "Effect": "Allow",
      "Action": [
        "s3:CreateBucket",
        "s3:GetBucketLocation",
        "s3:GetObject",
        "s3:ListBucket",
        "s3:PutObject"
      ],
      "Resource": [
        "arn:aws:s3:::aws-sam-cli-managed-default-*",
        "arn:aws:s3:::aws-sam-cli-managed-default-*/*"
      ]
    },
    {
      "Sid": "ReadDiagnostics",
      "Effect": "Allow",
      "Action": [
        "cloudformation:ListStacks",
        "codebuild:BatchGetBuilds",
        "codebuild:ListBuildsForProject",
        "ec2:DescribeInstances",
        "ec2:DescribeSubnets",
        "ec2:DescribeVpcs",
        "imagebuilder:GetImage",
        "imagebuilder:ListImagePipelineImages",
        "lambda:GetFunction",
        "lambda:GetFunctionConfiguration",
        "lambda:ListFunctions",
        "logs:DescribeLogGroups",
        "logs:DescribeLogStreams",
        "logs:GetLogEvents",
        "ssm:DescribeInstanceInformation"
      ],
      "Resource": "*"
    }
  ]
}
```

## Deploy

```bash
AWS_REGION=us-east-2 \
STACK_NAME=zerollm \
CFN_ROLE_ARN=arn:aws:iam::265978616089:role/ZeroLLMDevCloudFormationExecutionRole \
make deploy
```
