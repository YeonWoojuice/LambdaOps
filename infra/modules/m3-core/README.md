# M3 incident core infrastructure

This module connects the three M2 CloudWatch alarms to an SQS-backed incident ingester and exposes authenticated incident reads. It creates one DynamoDB table, an EventBridge rule, an ingest queue, separate delivery and consumer dead-letter queues, two Python Lambdas, and an HTTP API. It does not create an AWS environment, publish the Slash metrics, or deploy itself.

## Inputs and code contract

- Pass the existing M2 module's `alarm_arns` output directly. The map must contain `api_5xx_rate`, `api_average_latency`, and `db_average_query_latency`; the ARNs must be in this provider's account and region.
- Build the two ZIP files before planning. Each ZIP must place the `lambdaops_m3/` package at its root. Both may point to the same ZIP if it contains both handlers and their dependencies. `source_code_hash` tracks ZIP changes.
- The ingest handler is `lambdaops_m3.ingest_handler.lambda_handler`. Its environment contains `INCIDENTS_TABLE_NAME`, `ALARM_ARNS` (a JSON array of allowed ARNs), `SERVICE`, and `ENVIRONMENT`. It receives an SQS batch whose `body` is the full EventBridge alarm state-change JSON.
- The read handler is `lambdaops_m3.read_handler.lambda_handler`. Its environment contains `INCIDENTS_TABLE_NAME` and `REQUIRED_READ_SCOPE`. API Gateway invokes it with HTTP API payload format 2.0.
- The table has String `PK` and `SK` keys plus `GSI1PK` and `GSI1SK` String keys for index `GSI1` (`ALL` projection). The application owns the item layout. There is no second incident database.

```hcl
module "slash_m3_core" {
  source = "../../modules/m3-core"

  service                 = "slash-api"
  environment             = "dev"
  alarm_arns              = module.slash_m2_alarms.alarm_arns
  ingest_package_zip_path = abspath("${path.module}/build/m3-ingest.zip")
  read_package_zip_path   = abspath("${path.module}/build/m3-read.zip")
  jwt_issuer              = var.jwt_issuer
  jwt_audience            = var.jwt_audience
}
```

## Delivery and access

The EventBridge rule accepts only `aws.cloudwatch` / `CloudWatch Alarm State Change` events whose resource ARN matches one of the three M2 alarms and whose new state is `ALARM`, `OK`, or `INSUFFICIENT_DATA`. It does not route alarm configuration changes. EventBridge can retry failed delivery and sends exhausted events to its own dead-letter queue. The ingest queue retries failed Lambda records; after five receives it moves them to a separate consumer dead-letter queue. The Lambda event source mapping enables `ReportBatchItemFailures`, so the handler must return `{"batchItemFailures":[{"itemIdentifier":"<messageId>"}]}` for each failed record. The ingester still needs idempotent writes because SQS can redeliver a successful message.

The source queue visibility timeout is six times the configurable ingest Lambda timeout. Both queues and the incident table use encryption at rest; the table has point-in-time recovery. Each Lambda has its own IAM role. The read role can only read from the incident table and its `GSI1` index. Lambda log groups retain logs for 30 days.

The HTTP API publishes only `GET /incidents` and `GET /incidents/{incidentId}`. Both require API Gateway JWT authorization using the supplied HTTPS issuer, audience, and `incidents:read` scope by default. Set `read_authorization_scope` if the existing identity provider issues a different read scope; the same value is passed to the read handler. The read handler should use API Gateway's validated request context and never trust caller-supplied identity headers. No authentication provider is created by this module.

## Verification boundary

Run fixture-based local tests against the handlers before deployment. `terraform fmt` and `terraform validate` check the module's configuration, but do not prove AWS delivery or authentication. Actual M2 alarm deployment and M3 end-to-end replay in AWS remain separate environment verification steps. Before enabling a real environment, confirm the issuer/audience and chosen read scope, build the ZIPs, review `terraform plan`, deploy, then inspect both dead-letter queues and test ALARM, duplicate, OK, INSUFFICIENT_DATA, and out-of-order events.
