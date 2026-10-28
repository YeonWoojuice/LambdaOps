output "incident_table_name" {
  description = "Legacy DynamoDB table name; null in DSQL mode."
  value       = try(aws_dynamodb_table.incidents[0].name, null)
}

output "incident_table_arn" {
  description = "Legacy DynamoDB table ARN; null in DSQL mode."
  value       = try(aws_dynamodb_table.incidents[0].arn, null)
}

output "alarm_event_rule_arn" {
  description = "EventBridge rule accepting the three M2 alarm state changes."
  value       = try(aws_cloudwatch_event_rule.alarm_state_change[0].arn, null)
}

output "ingest_queue_url" {
  description = "URL of the SQS queue consumed by the M3 ingest Lambda."
  value       = aws_sqs_queue.ingest.id
}

output "ingest_queue_arn" {
  description = "ARN of the SQS queue consumed by the M3 ingest Lambda."
  value       = aws_sqs_queue.ingest.arn
}

output "eventbridge_dlq_url" {
  description = "Inspect this queue when EventBridge cannot deliver to the ingest queue."
  value       = aws_sqs_queue.eventbridge_dlq.id
}

output "consumer_dlq_url" {
  description = "Inspect this queue when a message fails ingest processing five times."
  value       = aws_sqs_queue.consumer_dlq.id
}

output "ingest_lambda_name" {
  description = "Name of the SQS ingest Lambda."
  value       = aws_lambda_function.ingest.function_name
}

output "read_lambda_name" {
  description = "Name of the authenticated HTTP read Lambda."
  value       = aws_lambda_function.read.function_name
}

output "http_api_endpoint" {
  description = "Base URL of the JWT-protected incident read API."
  value       = try(aws_apigatewayv2_api.incidents[0].api_endpoint, null)
}

output "dsql_cluster_id" {
  description = "Single-Region Incident DSQL cluster identifier; null in legacy mode."
  value       = try(aws_dsql_cluster.incidents[0].identifier, null)
}

output "dsql_cluster_arn" {
  description = "Exact DSQL cluster ARN allowed in runtime IAM policies."
  value       = try(aws_dsql_cluster.incidents[0].arn, null)
}

output "dsql_cluster_endpoint" {
  description = "PostgreSQL-compatible DSQL hostname, without protocol or credentials."
  value       = local.dsql_endpoint
}

output "ingest_role_arn" {
  description = "Ingest Lambda IAM role to map to the custom database write role."
  value       = aws_iam_role.ingest.arn
}

output "read_role_arn" {
  description = "Read Lambda IAM role to map to the custom database read role."
  value       = aws_iam_role.read.arn
}

output "dsql_ingest_db_user" {
  description = "Database role used by the ingest Lambda; null in legacy mode."
  value       = local.dsql_enabled ? var.dsql_ingest_db_user : null
}

output "dsql_read_db_user" {
  description = "Database role used by the read Lambda; null in legacy mode."
  value       = local.dsql_enabled ? var.dsql_read_db_user : null
}

output "dsql_bootstrap" {
  description = "Nonsecret identifiers for explicit schema/IAM role bootstrap outside Terraform state."
  value = local.dsql_enabled ? {
    account_id          = data.aws_caller_identity.current.account_id
    region              = data.aws_region.current.name
    cluster_id          = aws_dsql_cluster.incidents[0].identifier
    cluster_arn         = aws_dsql_cluster.incidents[0].arn
    endpoint            = local.dsql_endpoint
    ingest_iam_role_arn = aws_iam_role.ingest.arn
    read_iam_role_arn   = aws_iam_role.read.arn
    ingest_db_role      = var.dsql_ingest_db_user
    read_db_role        = var.dsql_read_db_user
  } : null
}

output "ingestion_active" {
  description = "Whether M2 event capture and SQS processing are enabled."
  value       = local.ingestion_active
}

output "http_api_active" {
  description = "Whether a JWT-protected HTTP API is provisioned."
  value       = local.http_api_active
}
