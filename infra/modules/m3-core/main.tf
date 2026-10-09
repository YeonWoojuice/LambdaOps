data "aws_caller_identity" "current" {}
data "aws_region" "current" {}

locals {
  # Keep Lambda, SQS, and IAM names within their service limits while retaining
  # a stable suffix if long service/environment names share the same prefix.
  name_prefix = "lambdaops-${substr(var.environment, 0, 12)}-${substr(var.service, 0, 15)}-${substr(sha1("${var.environment}:${var.service}"), 0, 8)}"
}

resource "aws_dynamodb_table" "incidents" {
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
  name        = "${local.name_prefix}-alarm-state"
  description = "Route only the three M2 CloudWatch alarm state changes to LambdaOps M3."

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
        ArnEquals = { "aws:SourceArn" = aws_cloudwatch_event_rule.alarm_state_change.arn }
      }
    }]
  })
}

resource "aws_sqs_queue_policy" "eventbridge_dlq" {
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
        ArnEquals = { "aws:SourceArn" = aws_cloudwatch_event_rule.alarm_state_change.arn }
      }
    }]
  })
}

resource "aws_cloudwatch_event_target" "ingest" {
  rule      = aws_cloudwatch_event_rule.alarm_state_change.name
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
    Statement = [
      {
        Sid      = "WriteIncidentTable"
        Effect   = "Allow"
        Action   = ["dynamodb:GetItem", "dynamodb:PutItem", "dynamodb:DeleteItem", "dynamodb:Query", "dynamodb:TransactWriteItems"]
        Resource = [aws_dynamodb_table.incidents.arn, "${aws_dynamodb_table.incidents.arn}/index/GSI1"]
      },
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
    ]
  })
}

resource "aws_iam_role_policy" "read" {
  name = "${local.name_prefix}-read"
  role = aws_iam_role.read.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid      = "ReadIncidentTable"
        Effect   = "Allow"
        Action   = ["dynamodb:GetItem", "dynamodb:Query"]
        Resource = [aws_dynamodb_table.incidents.arn, "${aws_dynamodb_table.incidents.arn}/index/GSI1"]
      },
      {
        Sid      = "WriteReadLogs"
        Effect   = "Allow"
        Action   = ["logs:CreateLogStream", "logs:PutLogEvents"]
        Resource = "${trimsuffix(aws_cloudwatch_log_group.read.arn, ":*")}:*"
      },
    ]
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
    variables = {
      INCIDENTS_TABLE_NAME = aws_dynamodb_table.incidents.name
      ALARM_ARNS           = jsonencode(values(var.alarm_arns))
      SERVICE              = var.service
      ENVIRONMENT          = var.environment
    }
  }

  depends_on = [aws_iam_role_policy.ingest, aws_cloudwatch_log_group.ingest]
  tags       = var.tags
}

resource "aws_lambda_function" "read" {
  function_name    = "${local.name_prefix}-read"
  role             = aws_iam_role.read.arn
  handler          = "lambdaops_m3.read_handler.lambda_handler"
  runtime          = "python3.12"
  timeout          = 10
  memory_size      = 256
  filename         = var.read_package_zip_path
  source_code_hash = filebase64sha256(var.read_package_zip_path)

  environment {
    variables = {
      INCIDENTS_TABLE_NAME = aws_dynamodb_table.incidents.name
      REQUIRED_READ_SCOPE  = var.read_authorization_scope
    }
  }

  depends_on = [aws_iam_role_policy.read, aws_cloudwatch_log_group.read]
  tags       = var.tags
}

resource "aws_lambda_event_source_mapping" "ingest" {
  event_source_arn        = aws_sqs_queue.ingest.arn
  function_name           = aws_lambda_function.ingest.arn
  batch_size              = 10
  function_response_types = ["ReportBatchItemFailures"]

  depends_on = [aws_iam_role_policy.ingest]
}

resource "aws_apigatewayv2_api" "incidents" {
  name          = "${local.name_prefix}-api"
  protocol_type = "HTTP"
  tags          = var.tags
}

resource "aws_apigatewayv2_authorizer" "jwt" {
  api_id           = aws_apigatewayv2_api.incidents.id
  name             = "${local.name_prefix}-jwt"
  authorizer_type  = "JWT"
  identity_sources = ["$request.header.Authorization"]

  jwt_configuration {
    issuer   = var.jwt_issuer
    audience = [var.jwt_audience]
  }
}

resource "aws_apigatewayv2_integration" "read" {
  api_id                 = aws_apigatewayv2_api.incidents.id
  integration_type       = "AWS_PROXY"
  integration_uri        = aws_lambda_function.read.invoke_arn
  payload_format_version = "2.0"
}

resource "aws_apigatewayv2_route" "list_incidents" {
  api_id               = aws_apigatewayv2_api.incidents.id
  route_key            = "GET /incidents"
  target               = "integrations/${aws_apigatewayv2_integration.read.id}"
  authorization_type   = "JWT"
  authorizer_id        = aws_apigatewayv2_authorizer.jwt.id
  authorization_scopes = [var.read_authorization_scope]
}

resource "aws_apigatewayv2_route" "get_incident" {
  api_id               = aws_apigatewayv2_api.incidents.id
  route_key            = "GET /incidents/{incidentId}"
  target               = "integrations/${aws_apigatewayv2_integration.read.id}"
  authorization_type   = "JWT"
  authorizer_id        = aws_apigatewayv2_authorizer.jwt.id
  authorization_scopes = [var.read_authorization_scope]
}

resource "aws_apigatewayv2_stage" "default" {
  api_id      = aws_apigatewayv2_api.incidents.id
  name        = "$default"
  auto_deploy = true
  tags        = var.tags
}

resource "aws_lambda_permission" "list_incidents" {
  statement_id  = "AllowApiListIncidents"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.read.function_name
  principal     = "apigateway.amazonaws.com"
  source_arn    = "${aws_apigatewayv2_api.incidents.execution_arn}/*/GET/incidents"
}

resource "aws_lambda_permission" "get_incident" {
  statement_id  = "AllowApiGetIncident"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.read.function_name
  principal     = "apigateway.amazonaws.com"
  source_arn    = "${aws_apigatewayv2_api.incidents.execution_arn}/*/GET/incidents/*"
}
