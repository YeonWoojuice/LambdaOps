output "incident_table_name" {
  description = "DynamoDB table consumed by both M3 Lambdas."
  value       = aws_dynamodb_table.incidents.name
}

output "incident_table_arn" {
  description = "ARN of the single M3 incident table."
  value       = aws_dynamodb_table.incidents.arn
}

output "alarm_event_rule_arn" {
  description = "EventBridge rule accepting the three M2 alarm state changes."
  value       = aws_cloudwatch_event_rule.alarm_state_change.arn
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
  value       = aws_apigatewayv2_api.incidents.api_endpoint
}
