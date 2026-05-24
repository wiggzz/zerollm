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

  account_id        = var.account_id
  aws_region        = var.aws_region
  environment       = "dev"
  github_org        = "wiggzz"
  github_repo       = "zerollm"
  role_prefix       = "zerollm"
  stack_prefix      = "zerollm"
  oidc_provider_arn = "arn:aws:iam::${var.account_id}:oidc-provider/token.actions.githubusercontent.com"

  allowed_github_subjects = [
    "repo:wiggzz/zerollm:environment:dev",
    "repo:wiggzz/zerollm:pull_request",
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
