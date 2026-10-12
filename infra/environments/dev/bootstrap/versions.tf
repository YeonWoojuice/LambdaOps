terraform {
  required_version = ">= 1.5.0"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = ">= 5.0, < 7.0"
    }
  }

  # Bootstrap starts with local state. After the bucket and lock table exist,
  # copy backend.s3.tf.example to backend.tf and migrate the state to S3.
}
