# This root stores its state in the S3 bucket created by bootstrap.
# Supply the actual bucket and lock table through ignored backend.hcl.
terraform {
  backend "s3" {}
}
