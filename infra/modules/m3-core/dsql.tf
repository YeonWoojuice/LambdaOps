resource "aws_dsql_cluster" "incidents" {
  count = local.dsql_enabled ? 1 : 0

  deletion_protection_enabled = true
  tags = merge(var.tags, {
    Name = "${local.name_prefix}-incidents"
  })

  lifecycle {
    prevent_destroy = true
    precondition {
      condition     = var.dsql_ingest_db_user != var.dsql_read_db_user
      error_message = "DSQL ingest and read database roles must be different."
    }
  }
}

# Existing deployments keep their resource addresses when opting into count.
# prevent_destroy on the legacy table stops a mode switch from deleting data.
moved {
  from = aws_dynamodb_table.incidents
  to   = aws_dynamodb_table.incidents[0]
}

moved {
  from = aws_cloudwatch_event_rule.alarm_state_change
  to   = aws_cloudwatch_event_rule.alarm_state_change[0]
}
moved {
  from = aws_sqs_queue_policy.ingest
  to   = aws_sqs_queue_policy.ingest[0]
}
moved {
  from = aws_sqs_queue_policy.eventbridge_dlq
  to   = aws_sqs_queue_policy.eventbridge_dlq[0]
}
moved {
  from = aws_cloudwatch_event_target.ingest
  to   = aws_cloudwatch_event_target.ingest[0]
}
moved {
  from = aws_apigatewayv2_api.incidents
  to   = aws_apigatewayv2_api.incidents[0]
}
moved {
  from = aws_apigatewayv2_authorizer.jwt
  to   = aws_apigatewayv2_authorizer.jwt[0]
}
moved {
  from = aws_apigatewayv2_integration.read
  to   = aws_apigatewayv2_integration.read[0]
}
moved {
  from = aws_apigatewayv2_route.list_incidents
  to   = aws_apigatewayv2_route.list_incidents[0]
}
moved {
  from = aws_apigatewayv2_route.get_incident
  to   = aws_apigatewayv2_route.get_incident[0]
}
moved {
  from = aws_apigatewayv2_stage.default
  to   = aws_apigatewayv2_stage.default[0]
}
moved {
  from = aws_lambda_permission.list_incidents
  to   = aws_lambda_permission.list_incidents[0]
}
moved {
  from = aws_lambda_permission.get_incident
  to   = aws_lambda_permission.get_incident[0]
}
