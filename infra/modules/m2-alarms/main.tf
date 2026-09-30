locals {
  namespace   = "LambdaOps/Slash"
  dimensions  = { Service = var.service, Environment = var.environment }
  name_prefix = "lambdaops-${var.environment}-${var.service}"
}

# All source data points are 60-second, standard-resolution sums. The publisher
# must send zero values for inactive periods so missing data means telemetry loss.
resource "aws_cloudwatch_metric_alarm" "api_5xx_rate" {
  alarm_name          = "${local.name_prefix}-api-5xx-rate"
  alarm_description   = "API 5xx percentage; evaluates only when RequestCount reaches the configured minimum."
  comparison_operator = "GreaterThanThreshold"
  threshold           = var.api_5xx_rate_threshold_percent
  evaluation_periods  = var.evaluation_periods
  datapoints_to_alarm = var.datapoints_to_alarm
  treat_missing_data  = "notBreaching"
  tags                = var.tags

  metric_query {
    id          = "rate"
    expression  = "IF(requests >= ${var.minimum_requests_per_period}, 100 * errors / requests, 0)"
    label       = "API 5xx rate (%)"
    return_data = true
  }

  metric_query {
    id          = "requests"
    return_data = false
    metric {
      metric_name = "RequestCount"
      namespace   = local.namespace
      period      = 60
      stat        = "Sum"
      unit        = "Count"
      dimensions  = local.dimensions
    }
  }

  metric_query {
    id          = "errors"
    return_data = false
    metric {
      metric_name = "ServerErrorCount"
      namespace   = local.namespace
      period      = 60
      stat        = "Sum"
      unit        = "Count"
      dimensions  = local.dimensions
    }
  }

  lifecycle {
    precondition {
      condition     = var.datapoints_to_alarm <= var.evaluation_periods
      error_message = "datapoints_to_alarm must not exceed evaluation_periods."
    }
  }
}

resource "aws_cloudwatch_metric_alarm" "api_average_latency" {
  alarm_name          = "${local.name_prefix}-api-average-latency"
  alarm_description   = "Mean API duration in ms; evaluates only when RequestCount reaches the configured minimum."
  comparison_operator = "GreaterThanThreshold"
  threshold           = var.api_average_latency_threshold_ms
  evaluation_periods  = var.evaluation_periods
  datapoints_to_alarm = var.datapoints_to_alarm
  treat_missing_data  = "notBreaching"
  tags                = var.tags

  metric_query {
    id          = "mean"
    expression  = "IF(requests >= ${var.minimum_requests_per_period}, duration / requests, 0)"
    label       = "Mean API duration (ms)"
    return_data = true
  }

  metric_query {
    id          = "requests"
    return_data = false
    metric {
      metric_name = "RequestCount"
      namespace   = local.namespace
      period      = 60
      stat        = "Sum"
      unit        = "Count"
      dimensions  = local.dimensions
    }
  }

  metric_query {
    id          = "duration"
    return_data = false
    metric {
      metric_name = "RequestDurationMsTotal"
      namespace   = local.namespace
      period      = 60
      stat        = "Sum"
      unit        = "Milliseconds"
      dimensions  = local.dimensions
    }
  }

  lifecycle {
    precondition {
      condition     = var.datapoints_to_alarm <= var.evaluation_periods
      error_message = "datapoints_to_alarm must not exceed evaluation_periods."
    }
  }
}

resource "aws_cloudwatch_metric_alarm" "db_average_query_latency" {
  alarm_name          = "${local.name_prefix}-db-average-query-latency"
  alarm_description   = "Mean DB query duration in ms; evaluates only when DBQueryCount reaches the configured minimum."
  comparison_operator = "GreaterThanThreshold"
  threshold           = var.db_average_query_latency_threshold_ms
  evaluation_periods  = var.evaluation_periods
  datapoints_to_alarm = var.datapoints_to_alarm
  treat_missing_data  = "notBreaching"
  tags                = var.tags

  metric_query {
    id          = "mean"
    expression  = "IF(queries >= ${var.minimum_db_queries_per_period}, duration / queries, 0)"
    label       = "Mean DB query duration (ms)"
    return_data = true
  }

  metric_query {
    id          = "queries"
    return_data = false
    metric {
      metric_name = "DBQueryCount"
      namespace   = local.namespace
      period      = 60
      stat        = "Sum"
      unit        = "Count"
      dimensions  = local.dimensions
    }
  }

  metric_query {
    id          = "duration"
    return_data = false
    metric {
      metric_name = "DBQueryDurationMsTotal"
      namespace   = local.namespace
      period      = 60
      stat        = "Sum"
      unit        = "Milliseconds"
      dimensions  = local.dimensions
    }
  }

  lifecycle {
    precondition {
      condition     = var.datapoints_to_alarm <= var.evaluation_periods
      error_message = "datapoints_to_alarm must not exceed evaluation_periods."
    }
  }
}
