terraform {
  required_version = ">= 1.10.0"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.0"
    }
  }
}

provider "aws" {
  region = var.aws_region
}

module "cicd" {
  source = "../../modules/cicd"

  account_id                  = var.account_id
  aws_region                  = var.aws_region
  environment                 = "prod"
  github_org                  = "wiggzz"
  github_repo                 = "zerollm"
  role_prefix                 = "zerollm"
  cloudformation_stack_prefix = "zerollm-prod"
  models_bucket_prefix        = "zerollm-models-prod"
  model_sync_project_prefix   = "zerollm-model-sync-prod"
  api_keys_table_prefix       = "zerollm-api-keys-prod"
  iam_resource_prefix         = "zerollm-prod"
  oidc_provider_arn           = "arn:aws:iam::${var.account_id}:oidc-provider/token.actions.githubusercontent.com"

  allowed_github_subjects = [
    "repo:wiggzz/zerollm:environment:prod",
  ]
}

variable "account_id" {
  type    = string
  default = "265978616089"
}

variable "aws_region" {
  type    = string
  default = "us-east-2"
}
