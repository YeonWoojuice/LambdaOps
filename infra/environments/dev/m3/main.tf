provider "aws" {
  region              = var.aws_region
  allowed_account_ids = [var.aws_account_id]
}

# Read the deployed M2 root's output; do not copy or reconstruct alarm ARNs.
data "terraform_remote_state" "m2" {
  backend = "s3"
  config = {
    bucket  = var.m2_state_bucket
    key     = var.m2_state_key
    region  = var.m2_state_region
    encrypt = true
  }
}

module "slash_m3_core" {
  source = "../../../modules/m3-core"

  service                  = var.service
  environment              = var.environment
  alarm_arns               = data.terraform_remote_state.m2.outputs.alarm_arns
  ingest_package_zip_path  = abspath(var.ingest_package_zip_path)
  read_package_zip_path    = abspath(var.read_package_zip_path)
  jwt_issuer               = var.jwt_issuer
  jwt_audience             = var.jwt_audience
  read_authorization_scope = var.read_authorization_scope
}
