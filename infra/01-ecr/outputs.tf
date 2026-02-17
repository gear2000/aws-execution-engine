output "ecr_repo_url" {
  description = "ECR repository URL"
  value       = aws_ecr_repository.iac_ci.repository_url
}

output "ecr_repo_arn" {
  description = "ECR repository ARN"
  value       = aws_ecr_repository.iac_ci.arn
}
