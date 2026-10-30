data "aws_caller_identity" "current" {}
data "aws_region" "current" {}

locals {
  # Keep Lambda, SQS, and IAM names within their service limits while retaining
  # a stable suffix if long service/environment names share the same prefix.
  name_prefix      = "lambdaops-${substr(var.environment, 0, 12)}-${substr(var.service, 0, 15)}-${substr(sha1("${var.environment}:${var.service}"), 0, 8)}"
  dsql_enabled     = var.incident_store == "dsql"
  storage_ready    = !local.dsql_enabled || var.dsql_schema_ready
  ingestion_active = var.ingestion_enabled && local.storage_ready && var.connect_m2_alarms
  http_api_active  = var.enable_http_api && local.storage_ready
  dsql_endpoint    = local.dsql_enabled ? "${aws_dsql_cluster.incidents[0].identifier}.dsql.${data.aws_region.current.name}.on.aws" : null

  database_environment = local.dsql_enabled ? {
    INCIDENT_STORE = "dsql"
    DSQL_ENDPOINT  = local.dsql_endpoint
    DSQL_REGION    = data.aws_region.current.name
    } : {
    INCIDENT_STORE       = "dynamodb"
    INCIDENTS_TABLE_NAME = aws_dynamodb_table.incidents[0].name
  }

  ingest_database_statements = local.dsql_enabled ? [{
    Sid      = "ConnectIncidentDatabase"
    Effect   = "Allow"
    Action   = ["dsql:DbConnect"]
    Resource = [aws_dsql_cluster.incidents[0].arn]
    }] : [{
    Sid      = "WriteIncidentTable"
    Effect   = "Allow"
    Action   = ["dynamodb:GetItem", "dynamodb:PutItem", "dynamodb:DeleteItem", "dynamodb:Query", "dynamodb:TransactWriteItems"]
    Resource = [aws_dynamodb_table.incidents[0].arn, "${aws_dynamodb_table.incidents[0].arn}/index/GSI1"]
  }]
  read_database_statements = local.dsql_enabled ? [{
    Sid      = "ConnectIncidentDatabase"
    Effect   = "Allow"
    Action   = ["dsql:DbConnect"]
    Resource = [aws_dsql_cluster.incidents[0].arn]
    }] : [{
    Sid      = "ReadIncidentTable"
    Effect   = "Allow"
    Action   = ["dynamodb:GetItem", "dynamodb:Query"]
    Resource = [aws_dynamodb_table.incidents[0].arn, "${aws_dynamodb_table.incidents[0].arn}/index/GSI1"]
  }]
}

resource "aws_dynamodb_table" "incidents" {
  count        = local.dsql_enabled ? 0 : 1
  name         = "${local.name_prefix}-incidents"
  billing_mode = "PAY_PER_REQUEST"
  hash_key     = "PK"
  range_key    = "SK"

  attribute {
    name = "PK"
    type = "S"
  }

  attribute {
    name = "SK"
    type = "S"
  }

  attribute {
    name = "GSI1PK"
    type = "S"
  }

  attribute {
    name = "GSI1SK"
    type = "S"
  }

  global_secondary_index {
    name            = "GSI1"
    hash_key        = "GSI1PK"
    range_key       = "GSI1SK"
    projection_type = "ALL"
  }

  point_in_time_recovery {
    enabled = true
  }

  server_side_encryption {
    enabled = true
  }

  tags = var.tags

  lifecycle {
    prevent_destroy = true
  }
}

resource "aws_sqs_queue" "eventbridge_dlq" {
  name                      = "${local.name_prefix}-eventbridge-dlq"
  message_retention_seconds = 1209600
  sqs_managed_sse_enabled   = true
  tags                      = var.tags
}

resource "aws_sqs_queue" "consumer_dlq" {
  name                      = "${local.name_prefix}-consumer-dlq"
  message_retention_seconds = 1209600
  sqs_managed_sse_enabled   = true
  tags                      = var.tags
}

resource "aws_sqs_queue" "ingest" {
  name                       = "${local.name_prefix}-ingest"
  visibility_timeout_seconds = var.ingest_timeout_seconds * 6
  message_retention_seconds  = 345600
  receive_wait_time_seconds  = 20
  sqs_managed_sse_enabled    = true

  redrive_policy = jsonencode({
    deadLetterTargetArn = aws_sqs_queue.consumer_dlq.arn
    maxReceiveCount     = 5
  })

  tags = var.tags
}

resource "aws_sqs_queue_redrive_allow_policy" "consumer_dlq" {
  queue_url = aws_sqs_queue.consumer_dlq.id
  redrive_allow_policy = jsonencode({
    redrivePermission = "byQueue"
    sourceQueueArns   = [aws_sqs_queue.ingest.arn]
  })
}

resource "aws_cloudwatch_event_rule" "alarm_state_change" {
  count       = var.connect_m2_alarms ? 1 : 0
  name        = "${local.name_prefix}-alarm-state"
  description = "Route only the three M2 CloudWatch alarm state changes to LambdaOps M3."
  state       = local.ingestion_active ? "ENABLED" : "DISABLED"

  event_pattern = jsonencode({
    source        = ["aws.cloudwatch"]
    "detail-type" = ["CloudWatch Alarm State Change"]
    resources     = values(var.alarm_arns)
    detail = {
      state = {
        value = ["ALARM", "OK", "INSUFFICIENT_DATA"]
      }
    }
  })

  lifecycle {
    precondition {
      condition = alltrue([
        for arn in values(var.alarm_arns) :
        split(":", arn)[3] == data.aws_region.current.name &&
        split(":", arn)[4] == data.aws_caller_identity.current.account_id
      ])
      error_message = "The M2 alarm ARNs must belong to the AWS account and region of this module's provider."
    }
  }

  tags = var.tags
}

resource "aws_sqs_queue_policy" "ingest" {
  count     = var.connect_m2_alarms ? 1 : 0
  queue_url = aws_sqs_queue.ingest.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Sid       = "AllowThisEventBridgeRule"
      Effect    = "Allow"
      Principal = { Service = "events.amazonaws.com" }
      Action    = "sqs:SendMessage"
      Resource  = aws_sqs_queue.ingest.arn
      Condition = {
        ArnEquals = { "aws:SourceArn" = aws_cloudwatch_event_rule.alarm_state_change[0].arn }
      }
    }]
  })
}

resource "aws_sqs_queue_policy" "eventbridge_dlq" {
  count     = var.connect_m2_alarms ? 1 : 0
  queue_url = aws_sqs_queue.eventbridge_dlq.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Sid       = "AllowThisEventBridgeRuleFailures"
      Effect    = "Allow"
      Principal = { Service = "events.amazonaws.com" }
      Action    = "sqs:SendMessage"
      Resource  = aws_sqs_queue.eventbridge_dlq.arn
      Condition = {
        ArnEquals = { "aws:SourceArn" = aws_cloudwatch_event_rule.alarm_state_change[0].arn }
      }
    }]
  })
}

resource "aws_cloudwatch_event_target" "ingest" {
  count     = var.connect_m2_alarms ? 1 : 0
  rule      = aws_cloudwatch_event_rule.alarm_state_change[0].name
  target_id = "M3IngestQueue"
  arn       = aws_sqs_queue.ingest.arn

  dead_letter_config {
    arn = aws_sqs_queue.eventbridge_dlq.arn
  }

  retry_policy {
    maximum_event_age_in_seconds = 3600
    maximum_retry_attempts       = 24
  }

  depends_on = [
    aws_sqs_queue_policy.ingest,
    aws_sqs_queue_policy.eventbridge_dlq,
  ]
}

resource "aws_iam_role" "ingest" {
  name = "${local.name_prefix}-ingest-role"
  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { Service = "lambda.amazonaws.com" }
      Action    = "sts:AssumeRole"
    }]
  })
  tags = var.tags
}

resource "aws_iam_role" "read" {
  name = "${local.name_prefix}-read-role"
  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { Service = "lambda.amazonaws.com" }
      Action    = "sts:AssumeRole"
    }]
  })
  tags = var.tags
}

resource "aws_cloudwatch_log_group" "ingest" {
  name              = "/aws/lambda/${local.name_prefix}-ingest"
  retention_in_days = 30
  tags              = var.tags
}

resource "aws_cloudwatch_log_group" "read" {
  name              = "/aws/lambda/${local.name_prefix}-read"
  retention_in_days = 30
  tags              = var.tags
}

resource "aws_iam_role_policy" "ingest" {
  name = "${local.name_prefix}-ingest"
  role = aws_iam_role.ingest.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = concat(local.ingest_database_statements, [
      {
        Sid      = "ConsumeIngestQueue"
        Effect   = "Allow"
        Action   = ["sqs:ReceiveMessage", "sqs:DeleteMessage", "sqs:GetQueueAttributes"]
        Resource = aws_sqs_queue.ingest.arn
      },
      {
        Sid      = "WriteIngestLogs"
        Effect   = "Allow"
        Action   = ["logs:CreateLogStream", "logs:PutLogEvents"]
        Resource = "${trimsuffix(aws_cloudwatch_log_group.ingest.arn, ":*")}:*"
      },
    ])
  })
}

resource "aws_iam_role_policy" "read" {
  name = "${local.name_prefix}-read"
  role = aws_iam_role.read.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = concat(local.read_database_statements, [
      {
        Sid      = "WriteReadLogs"
        Effect   = "Allow"
        Action   = ["logs:CreateLogStream", "logs:PutLogEvents"]
        Resource = "${trimsuffix(aws_cloudwatch_log_group.read.arn, ":*")}:*"
      },
    ])
  })
}

resource "aws_lambda_function" "ingest" {
  function_name    = "${local.name_prefix}-ingest"
  role             = aws_iam_role.ingest.arn
  handler          = "lambdaops_m3.ingest_handler.lambda_handler"
  runtime          = "python3.12"
  timeout          = var.ingest_timeout_seconds
  memory_size      = 256
  filename         = var.ingest_package_zip_path
  source_code_hash = filebase64sha256(var.ingest_package_zip_path)

  environment {
    variables = merge(local.database_environment, local.dsql_enabled ? {
      DSQL_DB_USER = var.dsql_ingest_db_user
      } : {}, {
      ALARM_ARNS  = jsonencode(values(var.alarm_arns))
      SERVICE     = var.service
      ENVIRONMENT = var.environment
    })
  }

  depends_on = [aws_iam_role_policy.ingest, aws_cloudwatch_log_group.ingest]
  tags       = var.tags
}

resource "aws_lambda_function" "read" {
  function_name    = "${local.name_prefix}-read"
  role             = aws_iam_role.read.arn
  handler          = "lambdaops_m3.read_handler.lambda_handler"
  runtime          = "python3.12"
  timeout          = var.read_timeout_seconds
  memory_size      = 256
  filename         = var.read_package_zip_path
  source_code_hash = filebase64sha256(var.read_package_zip_path)

  environment {
    variables = merge(local.database_environment, local.dsql_enabled ? {
      DSQL_DB_USER = var.dsql_read_db_user
      } : {}, {
      REQUIRED_READ_SCOPE = var.read_authorization_scope
    })
  }

  lifecycle {
    precondition {
      condition     = var.connect_m2_alarms ? length(var.alarm_arns) == 3 : length(var.alarm_arns) == 0
      error_message = "connect_m2_alarms requires exactly the three deployed M2 outputs; leave alarm_arns empty while disconnected."
    }
    precondition {
      condition = !var.enable_http_api || (
        can(regex("^https://[^[:space:]]+$", var.jwt_issuer)) &&
        can(regex("[^[:space:]]", var.jwt_audience))
      )
      error_message = "Enabling the HTTP API requires a real HTTPS JWT issuer and nonempty audience."
    }
  }

  depends_on = [aws_iam_role_policy.read, aws_cloudwatch_log_group.read]
  tags       = var.tags
}

resource "aws_lambda_event_source_mapping" "ingest" {
  event_source_arn        = aws_sqs_queue.ingest.arn
  function_name           = aws_lambda_function.ingest.arn
  batch_size              = var.ingest_batch_size
  enabled                 = local.ingestion_active
  function_response_types = ["ReportBatchItemFailures"]

  depends_on = [aws_iam_role_policy.ingest]
}

resource "aws_apigatewayv2_api" "incidents" {
  count         = local.http_api_active ? 1 : 0
  name          = "${local.name_prefix}-api"
  protocol_type = "HTTP"

  dynamic "cors_configuration" {
    for_each = var.http_api_cors_allowed_origin == null ? [] : [var.http_api_cors_allowed_origin]
    content {
      allow_origins     = [cors_configuration.value]
      allow_methods     = ["GET", "OPTIONS"]
      allow_headers     = ["authorization"]
      allow_credentials = false
      max_age           = 300
    }
  }

  tags = var.tags
}

resource "aws_apigatewayv2_authorizer" "jwt" {
  count            = local.http_api_active ? 1 : 0
  api_id           = aws_apigatewayv2_api.incidents[0].id
  name             = "${local.name_prefix}-jwt"
  authorizer_type  = "JWT"
  identity_sources = ["$request.header.Authorization"]

  jwt_configuration {
    issuer   = var.jwt_issuer
    audience = [var.jwt_audience]
  }
}

resource "aws_apigatewayv2_integration" "read" {
  count                  = local.http_api_active ? 1 : 0
  api_id                 = aws_apigatewayv2_api.incidents[0].id
  integration_type       = "AWS_PROXY"
  integration_uri        = aws_lambda_function.read.invoke_arn
  payload_format_version = "2.0"
}

resource "aws_apigatewayv2_route" "list_incidents" {
  count                = local.http_api_active ? 1 : 0
  api_id               = aws_apigatewayv2_api.incidents[0].id
  route_key            = "GET /incidents"
  target               = "integrations/${aws_apigatewayv2_integration.read[0].id}"
  authorization_type   = "JWT"
  authorizer_id        = aws_apigatewayv2_authorizer.jwt[0].id
  authorization_scopes = [var.read_authorization_scope]
}

resource "aws_apigatewayv2_route" "get_incident" {
  count                = local.http_api_active ? 1 : 0
  api_id               = aws_apigatewayv2_api.incidents[0].id
  route_key            = "GET /incidents/{incidentId}"
  target               = "integrations/${aws_apigatewayv2_integration.read[0].id}"
  authorization_type   = "JWT"
  authorizer_id        = aws_apigatewayv2_authorizer.jwt[0].id
  authorization_scopes = [var.read_authorization_scope]
}

resource "aws_apigatewayv2_stage" "default" {
  count       = local.http_api_active ? 1 : 0
  api_id      = aws_apigatewayv2_api.incidents[0].id
  name        = "$default"
  auto_deploy = true
  tags        = var.tags
}

resource "aws_lambda_permission" "list_incidents" {
  count         = local.http_api_active ? 1 : 0
  statement_id  = "AllowApiListIncidents"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.read.function_name
  principal     = "apigateway.amazonaws.com"
  source_arn    = "${aws_apigatewayv2_api.incidents[0].execution_arn}/*/GET/incidents"
}

resource "aws_lambda_permission" "get_incident" {
  count         = local.http_api_active ? 1 : 0
  statement_id  = "AllowApiGetIncident"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.read.function_name
  principal     = "apigateway.amazonaws.com"
  source_arn    = "${aws_apigatewayv2_api.incidents[0].execution_arn}/*/GET/incidents/*"
}
