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

variable "cloudformation_stack_prefix" {
  type = string
}

variable "models_bucket_prefix" {
  type = string
}

variable "model_sync_project_prefix" {
  type = string
}

variable "api_keys_table_prefix" {
  type = string
}

variable "iam_resource_prefix" {
  type = string
}

variable "oidc_provider_arn" {
  type = string
}

variable "allowed_github_subjects" {
  type = list(string)
}
