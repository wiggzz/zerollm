variable "account_id" {
  type = string
}

variable "aws_region" {
  type = string
}

variable "environment" {
  type = string
}

variable "github_org" {
  type = string
}

variable "github_repo" {
  type = string
}

variable "role_prefix" {
  type = string
}

variable "stack_prefix" {
  type = string
}

variable "oidc_provider_arn" {
  type = string
}

variable "allowed_github_subjects" {
  type = list(string)
}
