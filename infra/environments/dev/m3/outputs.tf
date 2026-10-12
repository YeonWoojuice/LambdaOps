output "alarm_event_rule_arn" {
  description = "EventBridge rule receiving the deployed M2 alarm state changes."
  value       = module.slash_m3_core.alarm_event_rule_arn
}

output "incident_table_name" {
  description = "Single M3 Incident table."
  value       = module.slash_m3_core.incident_table_name
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
