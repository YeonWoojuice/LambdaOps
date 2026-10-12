output "state_bucket_name" {
  description = "Name to use as bucket in each root's S3 backend configuration."
  value       = aws_s3_bucket.state.bucket
}

output "lock_table_name" {
  description = "Name to use as dynamodb_table in each root's S3 backend configuration."
  value       = aws_dynamodb_table.state_lock.name
}

output "state_region" {
  description = "Region to use for the S3 state backend."
  value       = var.state_region
}
