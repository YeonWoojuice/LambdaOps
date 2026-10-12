provider "aws" {
  region              = var.aws_region
  allowed_account_ids = [var.aws_account_id]
}

module "slash_m2_alarms" {
  source = "../../../modules/m2-alarms"

  service                               = var.service
  environment                           = var.environment
  api_5xx_rate_threshold_percent        = var.api_5xx_rate_threshold_percent
  api_average_latency_threshold_ms      = var.api_average_latency_threshold_ms
  db_average_query_latency_threshold_ms = var.db_average_query_latency_threshold_ms
  minimum_requests_per_period           = var.minimum_requests_per_period
  minimum_db_queries_per_period         = var.minimum_db_queries_per_period
}
