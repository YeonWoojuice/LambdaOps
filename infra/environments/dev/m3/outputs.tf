output "alarm_event_rule_arn" {
  description = "EventBridge rule receiving the deployed M2 alarm state changes."
  value       = module.slash_m3_core.alarm_event_rule_arn
}

output "incident_table_name" {
  description = "Legacy DynamoDB Incident table, or null in DSQL mode."
  value       = module.slash_m3_core.incident_table_name
}

output "dsql_bootstrap" {
  description = "Non-secret config for backend/scripts/bootstrap_dsql.py; export with terraform output -json dsql_bootstrap."
  value       = module.slash_m3_core.dsql_bootstrap
}

output "dsql_cluster_endpoint" {
  description = "Dedicated LambdaOps DSQL endpoint."
  value       = module.slash_m3_core.dsql_cluster_endpoint
}

output "ingest_lambda_name" {
  value = module.slash_m3_core.ingest_lambda_name
}

output "read_lambda_name" {
  value = module.slash_m3_core.read_lambda_name
}

output "ingestion_active" {
  description = "True only after storage readiness and real M2 Alarm wiring are enabled."
  value       = module.slash_m3_core.ingestion_active
}

output "http_api_active" {
  description = "True only after storage readiness and an existing JWT issuer are configured."
  value       = module.slash_m3_core.http_api_active
}

output "ingest_queue_url" {
  description = "Queue for fixture replay into the M3 ingest Lambda."
  value       = module.slash_m3_core.ingest_queue_url
}

output "eventbridge_dlq_url" {
  description = "Queue for failed EventBridge deliveries."
  value       = module.slash_m3_core.eventbridge_dlq_url
}

output "consumer_dlq_url" {
  description = "Queue for repeatedly failed ingest messages."
  value       = module.slash_m3_core.consumer_dlq_url
}

output "http_api_endpoint" {
  description = "Base URL of the JWT-protected Incident read API."
  value       = module.slash_m3_core.http_api_endpoint
}
