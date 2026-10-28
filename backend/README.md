# M3 Incident core

## Storage decision — 2026-10-28

Aurora DSQL is the selected Incident database. The approved three-table schema
is defined in [DSQL creation SQL](sql/dsql/README.md). Its adapter implements
stable UUID IDs, canonical redirects, atomic evidence/projection writes and
bounded whole-transaction retries. The list API supports page numbers, filters,
literal substring search, sorting and exact totals in one snapshot.
See the [API contract](../docs/contracts/incident-api.md).

Terraform now defaults to DSQL and explicitly sets `INCIDENT_STORE=dsql` in
both Lambda handlers. A dedicated dev cluster, runtime roles and two Lambdas
have been provisioned. The Python factory still accepts legacy `dynamodb`
configuration; it is not the current dev deployment. The frontend still uses
the cursor contract and needs a separate update. No automatic data migration
is provided. See the [AWS verification record](../docs/testing/m2-m3-verification.md)
for completed checks and the remaining event, JWT, concurrency and cost tests.

## DSQL connection settings

| Variable | Value |
| --- | --- |
| `INCIDENT_STORE` | `dsql` in both ingest and read Lambdas |
| `DSQL_ENDPOINT` | Cluster hostname, without a URL scheme |
| `DSQL_REGION` | Cluster region; falls back to `AWS_REGION` |
| `DSQL_DB_USER` | Custom runtime DB role; use separate ingest and read roles. `admin` is rejected. |
| `DSQL_CONNECT_TIMEOUT` | Optional 1–30 seconds, default 10 |
| `DSQL_SSLROOTCERT` | Optional CA bundle path; defaults to the packaged certifi bundle |

The connection factory signs a fresh DbConnect token using the Lambda IAM
credentials for each connection, verifies TLS hostname/certificate and opens a
short REPEATABLE READ transaction. Reads also use READ ONLY. Each context commits
or rolls back and closes; this initial implementation does not pool connections.
Tokens are not stored in settings or logged. Terraform grants each Lambda
`dsql:DbConnect` only for the dedicated cluster. The separate
[DB bootstrap tool](scripts/dsql-bootstrap.md) creates tables, custom DB roles,
SQL privileges and exact IAM role mappings without putting admin tokens in state.
[IAM authentication](https://docs.aws.amazon.com/aurora-dsql/latest/userguide/SECTION_authentication-token.html),
[supported BEGIN options](https://docs.aws.amazon.com/aurora-dsql/latest/userguide/working-with-postgresql-compatibility-supported-sql-features.html),
[concurrency and SQLSTATE 40001](https://docs.aws.amazon.com/aurora-dsql/latest/userguide/working-with-concurrency-control.html).

This package handles the three M2 CloudWatch alarms without changing the M2
module. The entry points are `lambdaops_m3.ingest_handler.lambda_handler`
(EventBridge → SQS → Lambda) and `lambdaops_m3.read_handler.lambda_handler`
(API Gateway HTTP API → Lambda). See
[`infra/modules/m3-core/README.md`](../infra/modules/m3-core/README.md) for the
AWS wiring. No AWS resources are created by running the local tests.

## Local verification

From this directory:

```sh
python3.12 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
python -m unittest discover -s tests -v
```

Run the separate [SQL integration suite](tests_dsql/README.md) against a disposable
local database. CI now includes this suite and the schema constraints.

The JSON fixtures cover ALARM, OK, INSUFFICIENT_DATA, a duplicate ALARM,
and a contradictory older ALARM. Replay tests deliver valid transitions in
every order, including two consecutive Alarm episodes and a return from
INSUFFICIENT_DATA to ALARM. The tests also exercise SQS partial failures,
authenticated list/detail behavior, and a DynamoDB transaction test double.

## Shared rules and legacy DynamoDB behavior

- Only `aws.cloudwatch` / `CloudWatch Alarm State Change` events for the three
  configured M2 alarm ARNs are accepted. The internal transition contains the
  account, region, ARN, state, canonical UTC transition time, and EventBridge
  event ID. The dedupe key is the SHA-256 digest of account, region, ARN,
  state, and transition time. Delivery time and SQS message ID are not identity.
- An ALARM begins an Incident for that Alarm episode. A subsequent ALARM
  after INSUFFICIENT_DATA remains in the same episode. OK records
  `alarmState=OK` and `alarmOkAt`, then ends that Alarm episode. A later ALARM
  begins a new Incident. `incidentState=NEW` is a separate field and is not
  changed to Resolved by OK.
- INSUFFICIENT_DATA changes the Alarm state history but does not end the
  episode or set `alarmOkAt`. The event contract requires the previous
  state and its transition time from the CloudWatch state-change payload. The projection
  follows those links, including missing predecessors inferred from later
  events, so replaying the same valid history in a different delivery order
  yields the same episode states. Contradictory transition evidence is rejected
  for retry and DLQ investigation instead of changing a known Incident.
- One DynamoDB table holds immutable event evidence, a per-Alarm revision, and
  Incident projections. A conditional transaction writes each new event and
  its Incident changes together. The per-Alarm revision prevents concurrent
  consumers from publishing an older view. The table has on-demand billing,
  encryption, and point-in-time recovery in Terraform. Retention duration is
  an environment decision to make before AWS deployment. An event that would
  exceed DynamoDB's transaction or item limits fails the SQS record, so the
  consumer dead-letter queue preserves it for investigation.
- The authenticated Detail response includes the Incident's linked state
  history and original EventBridge envelopes as evidence. It does not expose
  analysis, approval, or recovery results; those belong to later milestones.
  Detail uses strongly consistent table reads. The list uses a DynamoDB index,
  so a newly written Incident can appear there after a short delay in AWS.

## Read authorization

The Terraform module uses an existing OpenID Connect issuer with API Gateway
JWT validation. Both read routes require a configurable `incidents:read` scope.
The read Lambda uses only API Gateway's validated JWT context and returns 401
or 403 when identity or read scope is absent. It does not parse an unverified
Bearer header. The ingest and read Lambdas have separate IAM roles; the read
role has no write permission. The issuer, audience, and actual token must be
chosen and tested during environment verification.

## Building a deployment ZIP

The two Lambda ZIP inputs can point to the same package. The ZIP root must
contain `lambdaops_m3/` and its dependencies. Build it with Python 3.12 in a
Linux environment that matches the Lambda runtime. Include `boto3` from
`requirements.txt` in the ZIP so the SDK version is controlled. The Terraform
module consumes the resulting path; it does not run a package builder.
Use the [Docker-based ZIP builder](scripts/README.md) to build and smoke-test
that deployment package.

The M2 alarms have not been deployed or replayed in AWS. The dev SQS consumer
remains disabled and no HTTP API is exposed until real M2 outputs and an existing
JWT issuer are configured. CloudWatch delivery, queue retries and real JWT
integration still need AWS environment verification. Follow the
[staged dev deployment guide](../infra/environments/dev/README.md).
