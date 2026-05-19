output "github_deploy_role_arn" {
  value = aws_iam_role.github_deploy.arn
}

output "cloudformation_execution_role_arn" {
  value = aws_iam_role.cloudformation_execution.arn
}
