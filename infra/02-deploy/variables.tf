variable "image_tag" {
  description = "Docker image tag (typically git SHA)"
  type        = string
}

variable "ecr_repo" {
  description = "ECR repository URL"
  type        = string
}

variable "aws_region" {
  description = "AWS region"
  type        = string
}
