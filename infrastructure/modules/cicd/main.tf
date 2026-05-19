locals {
  app_name                         = var.role_prefix
  deploy_role_name                 = "${var.role_prefix}-deployment-role-${var.environment}"
  deploy_policy_name               = "${var.role_prefix}-deployment-role-${var.environment}-policy"
  cloudformation_role_name         = "${var.role_prefix}-cloudformation-execution-role-${var.environment}"
  cloudformation_policy_name       = "${var.role_prefix}-cloudformation-execution-role-${var.environment}-policy"
  cloudformation_stack_arn_pattern = "arn:aws:cloudformation:${var.aws_region}:${var.account_id}:stack/${var.stack_prefix}*/*"
  model_bucket_arn_pattern         = "arn:aws:s3:::${var.stack_prefix}-models-*-${var.account_id}"
  sam_bucket_arn_pattern           = "arn:aws:s3:::aws-sam-cli-managed-default-samclisourcebucket-*"
}

resource "aws_iam_role" "github_deploy" {
  name = local.deploy_role_name

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect = "Allow"
        Principal = {
          Federated = var.oidc_provider_arn
        }
        Action = "sts:AssumeRoleWithWebIdentity"
        Condition = {
          StringEquals = {
            "token.actions.githubusercontent.com:aud" = "sts.amazonaws.com"
          }
          StringLike = {
            "token.actions.githubusercontent.com:sub" = var.allowed_github_subjects
          }
        }
      }
    ]
  })

  tags = {
    App   = local.app_name
    Stage = var.environment
  }
}

resource "aws_iam_role_policy_attachment" "github_deploy" {
  role       = aws_iam_role.github_deploy.name
  policy_arn = aws_iam_policy.github_deploy.arn
}

resource "aws_iam_policy" "github_deploy" {
  name   = local.deploy_policy_name
  policy = data.aws_iam_policy_document.github_deploy.json

  tags = {
    App   = local.app_name
    Stage = var.environment
  }
}

data "aws_iam_policy_document" "github_deploy" {
  statement {
    sid = "CloudFormationStackAccess"
    actions = [
      "cloudformation:CreateChangeSet",
      "cloudformation:CreateStack",
      "cloudformation:DeleteChangeSet",
      "cloudformation:DeleteStack",
      "cloudformation:DescribeChangeSet",
      "cloudformation:DescribeStackEvents",
      "cloudformation:DescribeStacks",
      "cloudformation:ExecuteChangeSet",
      "cloudformation:GetTemplateSummary",
      "cloudformation:ListStackResources",
      "cloudformation:UpdateStack",
      "cloudformation:ValidateTemplate",
    ]
    resources = [local.cloudformation_stack_arn_pattern]
  }

  statement {
    sid = "CloudFormationBootstrapRead"
    actions = [
      "cloudformation:DescribeStacks",
      "cloudformation:ListStacks",
    ]
    resources = ["*"]
  }

  statement {
    sid       = "PassCloudFormationExecutionRole"
    actions   = ["iam:PassRole"]
    resources = [aws_iam_role.cloudformation_execution.arn]
  }

  statement {
    sid = "SamArtifactBucketAccess"
    actions = [
      "s3:CreateBucket",
      "s3:GetBucketLocation",
      "s3:GetObject",
      "s3:ListBucket",
      "s3:PutBucketVersioning",
      "s3:PutObject",
    ]
    resources = [
      local.sam_bucket_arn_pattern,
      "${local.sam_bucket_arn_pattern}/*",
    ]
  }

  statement {
    sid = "ModelSyncArtifactAccess"
    actions = [
      "s3:GetObject",
      "s3:ListBucket",
      "s3:PutObject",
    ]
    resources = [
      local.model_bucket_arn_pattern,
      "${local.model_bucket_arn_pattern}/*",
    ]
  }

  statement {
    sid = "ModelSyncBuildAccess"
    actions = [
      "codebuild:BatchGetBuilds",
      "codebuild:StartBuild",
    ]
    resources = [
      "arn:aws:codebuild:${var.aws_region}:${var.account_id}:project/${var.stack_prefix}*-model-sync-*",
    ]
  }

  statement {
    sid = "DeployDiscovery"
    actions = [
      "ec2:DescribeSubnets",
      "imagebuilder:GetImage",
      "imagebuilder:ListImagePipelineImages",
    ]
    resources = ["*"]
  }
}

resource "aws_iam_role" "cloudformation_execution" {
  name = local.cloudformation_role_name

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect = "Allow"
        Principal = {
          Service = "cloudformation.amazonaws.com"
        }
        Action = "sts:AssumeRole"
      }
    ]
  })

  tags = {
    App   = local.app_name
    Stage = var.environment
  }
}

resource "aws_iam_role_policy_attachment" "cloudformation_execution" {
  role       = aws_iam_role.cloudformation_execution.name
  policy_arn = aws_iam_policy.cloudformation_execution.arn
}

resource "aws_iam_policy" "cloudformation_execution" {
  name   = local.cloudformation_policy_name
  policy = data.aws_iam_policy_document.cloudformation_execution.json

  tags = {
    App   = local.app_name
    Stage = var.environment
  }
}

data "aws_iam_policy_document" "cloudformation_execution" {
  statement {
    sid = "ServerlessStackManagement"
    actions = [
      "apigateway:*",
      "cloudformation:CreateChangeSet",
      "cloudformation:DescribeChangeSet",
      "cloudformation:ExecuteChangeSet",
      "codebuild:*",
      "dynamodb:*",
      "ec2:AuthorizeSecurityGroupIngress",
      "ec2:CreateSecurityGroup",
      "ec2:CreateTags",
      "ec2:DeleteSecurityGroup",
      "ec2:DescribeSecurityGroups",
      "ec2:DescribeVpcs",
      "ec2:RevokeSecurityGroupIngress",
      "events:*",
      "lambda:*",
      "logs:*",
      "s3:*",
      "secretsmanager:GetSecretValue",
    ]
    resources = ["*"]
  }

  statement {
    sid = "IamRoleManagement"
    actions = [
      "iam:AddRoleToInstanceProfile",
      "iam:AttachRolePolicy",
      "iam:CreateInstanceProfile",
      "iam:CreateRole",
      "iam:DeleteInstanceProfile",
      "iam:DeleteRole",
      "iam:DeleteRolePolicy",
      "iam:DetachRolePolicy",
      "iam:GetInstanceProfile",
      "iam:GetRole",
      "iam:GetRolePolicy",
      "iam:ListAttachedRolePolicies",
      "iam:ListInstanceProfilesForRole",
      "iam:ListRolePolicies",
      "iam:PassRole",
      "iam:PutRolePolicy",
      "iam:RemoveRoleFromInstanceProfile",
      "iam:TagInstanceProfile",
      "iam:TagRole",
      "iam:UntagRole",
      "iam:UpdateAssumeRolePolicy",
    ]
    resources = [
      "arn:aws:iam::${var.account_id}:role/${var.stack_prefix}*",
      "arn:aws:iam::${var.account_id}:instance-profile/${var.stack_prefix}*",
    ]
  }

  statement {
    sid = "ManagedPolicyReadForSamConnectors"
    actions = [
      "iam:GetPolicy",
      "iam:GetPolicyVersion",
    ]
    resources = [
      "arn:aws:iam::aws:policy/*",
    ]
  }
}
