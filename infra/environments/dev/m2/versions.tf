terraform {
  required_version = ">= 1.5.0"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = ">= 5.0, < 7.0"
    }
  }

  # Supply an existing bucket, a unique key, and an existing lock table at init.
  # This root does not create its own state infrastructure.
  backend "s3" {}
}
