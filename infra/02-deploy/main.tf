terraform {
  required_version = ">= 1.5"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = ">= 5.0"
    }
  }

  # backend.tf is generated at deploy time by scripts/generate_backend.sh
}

provider "aws" {
  region = var.aws_region
}

data "aws_caller_identity" "current" {}
data "aws_region" "current" {}

locals {
  prefix     = "aws-exe-sys"
  account_id = data.aws_caller_identity.current.account_id
  region     = data.aws_region.current.name
  image_uri  = "${var.ecr_repo}:${var.image_tag}"
  hash       = substr(sha256("${terraform.workspace}${var.aws_region}"), 0, 5)
}
