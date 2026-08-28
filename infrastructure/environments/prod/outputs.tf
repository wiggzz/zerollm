output "github_deploy_role_arn" {
  value = module.cicd.github_deploy_role_arn
}

output "cloudformation_execution_role_arn" {
  value = module.cicd.cloudformation_execution_role_arn
}
