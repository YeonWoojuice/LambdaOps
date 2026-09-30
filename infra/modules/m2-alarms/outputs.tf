output "alarm_names" {
  description = "Names of the three CloudWatch alarms for a later EventBridge rule."
  value = {
    api_5xx_rate             = aws_cloudwatch_metric_alarm.api_5xx_rate.alarm_name
    api_average_latency      = aws_cloudwatch_metric_alarm.api_average_latency.alarm_name
    db_average_query_latency = aws_cloudwatch_metric_alarm.db_average_query_latency.alarm_name
  }
}

output "alarm_arns" {
  description = "ARNs of the three CloudWatch alarms for a later EventBridge rule."
  value = {
    api_5xx_rate             = aws_cloudwatch_metric_alarm.api_5xx_rate.arn
    api_average_latency      = aws_cloudwatch_metric_alarm.api_average_latency.arn
    db_average_query_latency = aws_cloudwatch_metric_alarm.db_average_query_latency.arn
  }
}
