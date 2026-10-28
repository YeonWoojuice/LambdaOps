# M3 Incident core infrastructure

This module provisions the approved single-Region Aurora DSQL Incident database,
separate ingest/read IAM roles and Python 3.12 Lambdas, an ingest queue and two
dead-letter queues. M2 event capture and the JWT HTTP API are explicit activation
steps. It does not create Slash, metrics, Cognito, or database tables through a
Terraform provisioner. [DB decision](../../../docs/decisions/0001-incident-db.md).

## Provision, initialize, then activate

| Stage | Inputs | Result |
| --- | --- | --- |
| Provision | `incident_store="dsql"` (default), `dsql_schema_ready=false`, `connect_m2_alarms=false`, `enable_http_api=false` | DSQL, IAM roles, both Lambdas and queues exist. No EventBridge rule or public HTTP API; SQS processing is disabled. |
| Initialize | Export `dsql_bootstrap`; apply and verify the approved schema, ASYNC indexes, custom DB roles and exact IAM mappings using administrator credentials | Three tables and runtime SQL privileges are ready. Runtime Lambda roles are not administrators. |
| Connect M2 | Real M2 `alarm_arns`, `connect_m2_alarms=true`, `dsql_schema_ready=true` | EventBridge capture and SQS processing activate when `ingestion_enabled=true`. |
| Expose reads | `enable_http_api=true`, real `jwt_issuer`/`jwt_audience`, verified read scope, `dsql_schema_ready=true` | Only two JWT-protected GET routes are created. |

`dsql_schema_ready` records completed verification; setting it does not run SQL
or establish readiness itself. The [SQL guide](../../../backend/sql/dsql/README.md)
and [dev deployment guide](../../environments/dev/README.md) describe the workflow.
DB and Lambdas may be provisioned before M2 or the identity provider is ready.
Do not invent Alarm ARNs or issuer values to pass a plan.

```hcl
module "slash_m3_core" {
  source = "../../../modules/m3-core"

  service                 = "slash-api"
  environment             = "dev"
  ingest_package_zip_path = abspath("./build/m3-core.zip")
  read_package_zip_path   = abspath("./build/m3-core.zip")

  dsql_schema_ready = false
  connect_m2_alarms = false
  enable_http_api  = false
}
```

## Database and permissions

The DSQL cluster uses the provider region and service-default encryption.
Service deletion protection and Terraform `prevent_destroy` are enabled.
Changing these protections requires an explicit data-retention decision.

| Runtime | IAM permission | Database identity and SQL privileges |
| --- | --- | --- |
| Ingest Lambda | `dsql:DbConnect` on this exact cluster ARN, ingest SQS consumption, own log writes | `lambdaops_ingest` by default; SELECT and selected INSERT/UPDATE grants from bootstrap SQL |
| Read Lambda | `dsql:DbConnect` on this exact cluster ARN, own log writes | `lambdaops_read` by default; SELECT on the three tables only |

AWS IAM GRANT maps each Lambda IAM role to its own custom database role.
DbConnect permits connection; SQL grants determine table operations. DSQL has no
`dsql:DbUser` IAM condition key, so this module does not invent one. Neither
runtime role has DbConnectAdmin, schema administration or a DynamoDB grant in
DSQL mode. [AWS authorization](https://docs.aws.amazon.com/aurora-dsql/latest/userguide/using-database-and-iam-roles.html),
[supported IAM conditions](https://docs.aws.amazon.com/service-authorization/latest/reference/list_dsql.html).

The `dsql_bootstrap` output contains nonsecret account, region, cluster
identifier/ARN/endpoint, IAM role ARNs and database role names. No database password
or IAM token is stored in Terraform inputs/state by this procedure. The endpoint
uses the documented `{identifier}.dsql.{region}.on.aws` format.

## Lambda and event contract

Build a Linux x86_64 ZIP before planning with `lambdaops_m3/`, boto3, binary
psycopg 3 and the TLS root bundle. Both functions may use the same ZIP;
`source_code_hash` tracks changes. The handlers remain
`lambdaops_m3.ingest_handler.lambda_handler` and
`lambdaops_m3.read_handler.lambda_handler`.

Both receive `INCIDENT_STORE=dsql`, `DSQL_ENDPOINT`, `DSQL_REGION` and their own
`DSQL_DB_USER`. Ingest also receives `ALARM_ARNS`, `SERVICE` and `ENVIRONMENT`;
reads receive `REQUIRED_READ_SCOPE`. No VPC is attached: Lambda's managed network
reaches the TLS DSQL endpoint without a NAT Gateway. Configuration checks do not
prove actual connectivity or authorization.

When connected, `alarm_arns` must be the three deployed M2 outputs in this account
and region: `api_5xx_rate`, `api_average_latency`, `db_average_query_latency`.
When disconnected, it must be empty. Its empty allowlist makes direct ingest calls
fail closed until M2 is connected.

EventBridge accepts only CloudWatch Alarm State Change ALARM, OK and
INSUFFICIENT_DATA events for those ARNs. Delivery failures and exhausted consumer
retries use separate DLQs. SQS uses ReportBatchItemFailures and a max receive count
of five. The default batch is one because each record opens a short DSQL
transaction. Visibility is six times the ingest timeout. Log retention is 30 days.

The HTTP API exposes only `GET /incidents` and `GET /incidents/{incidentId}`
with JWT and the configured read scope (default `incidents:read`). No
unauthenticated alternative is created. Enabling the API requires a real issuer
and audience even if storage readiness still prevents creation.

## Existing DynamoDB deployments

`incident_store="dynamodb"` preserves the old adapter environment and IAM grants.
Existing resource addresses have moved declarations for count-based resources.
Keep `connect_m2_alarms=true` and `enable_http_api=true` for an existing active
legacy deployment; changing those flags disconnects capture or removes its API.
The legacy table has prevent_destroy, so switching an existing deployment to DSQL
cannot silently delete it. This module does not migrate data. The bootstrap
DynamoDB state-lock table is outside this module and unchanged.

## Verification boundary

AWS provider 5.100.0 introduced aws_dsql_cluster; the dev lock file selects a
concrete version. Run format/configuration checks and inspect the saved plan.
Actual schema initialization, IAM/SQL separation, Lambda connections, Alarm
delivery, JWT validation and query costs each require environment evidence.
A successful apply does not complete M3.
