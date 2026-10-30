provider "aws" {
  region              = var.aws_region
  allowed_account_ids = [var.aws_account_id]
}

# Provision DSQL and Lambdas before M2 if needed; never fabricate Alarm ARNs.
data "terraform_remote_state" "m2" {
  count   = var.connect_m2_alarms ? 1 : 0
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

  service                      = var.service
  environment                  = var.environment
  alarm_arns                   = var.connect_m2_alarms ? data.terraform_remote_state.m2[0].outputs.alarm_arns : {}
  connect_m2_alarms            = var.connect_m2_alarms
  incident_store               = var.incident_store
  dsql_schema_ready            = var.dsql_schema_ready
  enable_http_api              = var.enable_http_api
  http_api_cors_allowed_origin = var.http_api_cors_allowed_origin
  ingestion_enabled            = var.ingestion_enabled
  ingest_package_zip_path      = abspath(var.ingest_package_zip_path)
  read_package_zip_path        = abspath(var.read_package_zip_path)
  jwt_issuer                   = var.jwt_issuer
  jwt_audience                 = var.jwt_audience
  read_authorization_scope     = var.read_authorization_scope
  tags = {
    Project     = "LambdaOps"
    Environment = var.environment
    Service     = var.service
    ManagedBy   = "Terraform"
  }
}
