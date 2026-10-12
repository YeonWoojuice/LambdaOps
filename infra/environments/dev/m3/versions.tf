terraform {
  required_version = ">= 1.5.0"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = ">= 5.0, < 7.0"
    }
  }

  # Use a distinct state key from M2. The bucket and lock table pre-exist.
  backend "s3" {}
}
