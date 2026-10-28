# LambdaOps dev Terraform roots

## Deployment scope and current state — 2026-10-28

Aurora DSQL is the selected Incident database. The first M3 apply created 14
resources in account `642935793048`, Seoul (`ap-northeast-2`), including the
dedicated DSQL cluster `krugaq2rx2w52ku2bbp7e6piw4`, runtime IAM roles, queues and
two Lambdas. The approved schema and separate SQL roles/IAM mappings now pass
live verification. The read Lambda passes six direct-invocation checks, including
empty list/filter queries against DSQL. `dsql_schema_ready=true` records DB
readiness; `connect_m2_alarms=false` and `enable_http_api=false` remain unchanged.
There is no M2 state or deployed M2 Alarm, no EventBridge rule or public HTTP API,
and SQS processing is disabled. The ingest Lambda is configured but has not
written through an actual Alarm event. Synthetic authorizer context in a direct
Lambda invocation does not verify real JWT authentication. See the
[dated evidence](../../../docs/testing/m2-m3-verification.md).

The bootstrap DynamoDB state lock table serves Terraform state coordination; it
is not the Incident database. See the
[Incident DB decision](../../../docs/decisions/0001-incident-db.md) and the
[M3 module](../../modules/m3-core/README.md) for the DSQL configuration.

The `m2/` and `m3/` directories are independent Terraform roots. M3 storage and
Lambdas can be provisioned before M2; event ingestion is connected only after the
database is ready and the three real M2 Alarms exist. An optional `bootstrap/`
root creates state storage when no approved bucket and lock table already exist.
M2 and M3 use the reusable modules under `infra/modules/`; running Terraform from
`infra/` or a module directory is not the deployment path. The directory name
`dev` identifies this deployment target; it does not establish the metric's
`Environment` value. Set `service` and `environment` from the actual publisher
and use the same values in both roots. These roots do not create an AWS account,
Slash runtime, metric publisher or identity provider.

## Before either root

1. Choose the actual dev AWS account and deployment region. Use credentials for that account; the AWS provider checks `allowed_account_ids`. Do not put credentials in Terraform files.
2. Select an approved existing S3 state bucket and DynamoDB state lock table, or complete the optional bootstrap below first. Bucket versioning and encryption must be enabled; the lock table needs a String partition key named `LockID`. The bucket/lock table must be reachable by the chosen credentials. With Terraform 1.5.7, use the DynamoDB lock table shown in the backend examples. Give bootstrap, M2 and M3 different state keys. When M2 is connected, the M3 credentials must also read the M2 state object. Reading an M2 output through `terraform_remote_state` grants access to the entire M2 state snapshot, so keep sensitive data out of that state and restrict S3 access.
3. Before provisioning M3, build the Python 3.12 Lambda ZIP or ZIPs with `lambdaops_m3/` and the DSQL dependencies at the ZIP root, as described by the [M3 backend](../../../backend/README.md). Terraform requires existing ZIPs; it does not build them. A running Slash service and a JWT issuer are needed for later event/API validation, not for the initial DSQL provision.

### If Cognito is chosen for incident reads

Decide which people may read the Incident list and details before choosing a user pool. The M3 read API currently returns all Incidents to any caller with its read scope. A separate public app client within a shared pool limits which client ID the API accepts, but does not stop another user of that pool from signing in to the public client. For an operator-only user set, use a separate pool or explicitly design an additional user authorization check.

In the chosen pool, define a Cognito resource server with identifier `lambdaops` and scope `incidents.read`. The issued scope string is `lambdaops/incidents.read`, which must be the exact M3 `read_authorization_scope` value. Use an OAuth authorization-code public client with PKCE for the read test. Set `jwt_issuer` to the user pool issuer and `jwt_audience` to that client ID. Test with an OAuth **access token** that contains the custom scope; a token from `InitiateAuth` is not a substitute for this scope test. The [Cognito resource-server guide](https://docs.aws.amazon.com/cognito/latest/developerguide/cognito-user-pools-define-resource-servers.html) and [API Gateway JWT guide](https://docs.aws.amazon.com/apigateway/latest/developerguide/http-api-jwt-authorizer.html) describe these settings. Keep tokens out of Terraform files, Git, and test logs.

For M2 and M3, copy each root's `backend.s3.hcl.example` to ignored `backend.hcl`, and `dev.tfvars.example` to ignored `dev.tfvars`. Replace every placeholder. The sample state bucket and lock table names are deliberately unusable. Keep `m2/backend.hcl` and `m3/backend.hcl` on distinct keys. Never add access keys, secret keys, or tokens to these files.

## 0. Optional state bootstrap

Skip this section when an approved, accessible state bucket and lock table already exist. Otherwise, first obtain permission to create them in the chosen **state account and region**. The S3 bucket name must be globally unique. Bootstrap starts with a local state file because the remote backend does not yet exist. Keep that file and any backup protected; never commit or share it.

From `infra/environments/dev/bootstrap`, copy `bootstrap.tfvars.example` to ignored `bootstrap.tfvars`, fill the actual state account, region, bucket name, and lock table name, then run:

```sh
terraform init
terraform validate
terraform plan -var-file=bootstrap.tfvars -out=bootstrap.tfplan
```

Review the plan for exactly one private, encrypted, versioned S3 bucket and one DynamoDB lock table. After approval, apply it and record the three outputs:

```sh
terraform apply bootstrap.tfplan
terraform output
```

Only after both resources exist, copy `backend.s3.tf.example` to `backend.tf` and `backend.s3.hcl.example` to ignored `backend.hcl`. Fill `backend.hcl` from the actual outputs and keep its `bootstrap.tfstate` key distinct from the M2 and M3 keys. `backend.tf` contains only an empty S3 backend declaration; keep it in version control after migration so a fresh checkout does not silently start a new local state. Migrate bootstrap's local state:

```sh
terraform init -migrate-state -backend-config=backend.hcl
terraform state list
terraform plan -var-file=bootstrap.tfvars -detailed-exitcode
```

The final plan should exit with code 0 (no changes). Confirm the state object exists in S3 and its bucket versioning and encryption are enabled before securing or deleting any local state backup. Commit `bootstrap/backend.tf` before another checkout runs Terraform; keep `bootstrap/backend.hcl` and state backups untracked. Do not delete the bucket or lock table while any root uses them. Both resources have Terraform `prevent_destroy` protection; a mistaken destroy must stop. If migration fails, retain the local state and fix access or backend configuration before starting M2.

## 1. Verify and deploy M2 independently

M2 remains a separate environment verification task. Before applying this root,
have Slash running in AWS and publishing the five `LambdaOps/Slash` metrics
described by the [M2 module](../../modules/m2-alarms/README.md) every 60 seconds.
Enable the DB query timer in the actual runtime and confirm that all five
metrics have exactly the `Service` and `Environment` values supplied to M2.
Measure traffic before selecting alarm thresholds and minimum counts. This
repository does not contain the metric publisher or a Slash deployment stack.
If Slash is still local, continue with M3 provisioning below and leave its M2
connection disabled.

From `infra/environments/dev/m2`:

```sh
terraform init -backend-config=backend.hcl
terraform validate
terraform plan -var-file=dev.tfvars -out=m2.tfplan
```

Review the plan: it should create exactly the three M2 metric-math alarms. Confirm the selected `service` and `environment`, thresholds, and minimum counts against observed 60-second metrics before applying the reviewed plan:

```sh
terraform apply m2.tfplan
terraform output alarm_arns
```

Confirm the three alarms exist in the selected account and region. Run bounded, private fault tests for 5xx errors, API delay, and DB query delay; keep the ALARM and subsequent OK state history and corresponding metric data as evidence. An OK state ends that Alarm episode and does not prove the whole service recovered. Stop here and correct the metric publisher or M2 inputs if these checks fail.

## 2. Provision and initialize M3 in stages

| Stage | What is enabled | Required evidence before the next stage |
| --- | --- | --- |
| Provision | DSQL, IAM roles, queues and two Lambdas | Reviewed Terraform plan and successful apply |
| Initialize database | Three approved tables, indexes and separate runtime DB roles | Bootstrap `verify` passes for schema, indexes, privileges and IAM mappings |
| Connect M2 | EventBridge delivery and SQS processing | Three real M2 Alarm ARNs from its deployed remote state; database readiness verified |
| Enable HTTP | JWT-protected list/detail GET routes | Real issuer, audience and read scope; database readiness verified |
| Verify environment | Fixture replay, authenticated reads and controlled faults | Actual AWS results for each path; local tests alone are insufficient |

### 2.1. Provision DSQL and Lambda resources

In `m3/dev.tfvars`, set the actual account, region, service/environment and built
ZIP paths. Keep the initial gates below until their respective checks pass:

```hcl
incident_store    = "dsql"
dsql_schema_ready = false
connect_m2_alarms = false
enable_http_api   = false
```

M2 remote-state settings and JWT settings are unnecessary at this stage. From
`infra/environments/dev/m3`:

```sh
terraform init -backend-config=backend.hcl
terraform validate
terraform plan -var-file=dev.tfvars -out=m3.tfplan
```

Review the initial plan for one dedicated DSQL cluster, three queues, two
Lambdas, their IAM roles/policies and logs, and a disabled SQS event source
mapping. It must not create a DynamoDB Incident table, EventBridge rule or HTTP
API. Confirm that the runtime roles receive only `dsql:DbConnect` on this
cluster, not database administrator access. Apply the reviewed saved plan:

```sh
terraform apply m3.tfplan
terraform output
```

The first apply creates the cluster and connection settings, not SQL tables or
SQL permissions. DSQL deletion protection is enabled. Existing unrelated DSQL
clusters are not imported or reused by this root.

### 2.2. Initialize the approved schema and runtime permissions

From the repository root, export the nonsecret cluster/role identifiers to a
temporary file outside Git. Use a Python 3.12 environment with the backend
requirements installed and operator AWS credentials for the selected account.
The operator needs DSQL cluster inspection and administrator connection access;
these privileges are not assigned to the runtime Lambda roles.

```sh
terraform -chdir=infra/environments/dev/m3 output -json dsql_bootstrap > /private/tmp/lambdaops-dev-dsql-bootstrap.json
python backend/scripts/bootstrap_dsql.py preview --config /private/tmp/lambdaops-dev-dsql-bootstrap.json
```

`preview` lists the schema files and planned role/mapping statements without
contacting AWS. Check that the exported account, region, cluster and IAM role
ARNs identify this deployment. Then execute the stages in order; stop if any
command fails:

```sh
python backend/scripts/bootstrap_dsql.py schema --config /private/tmp/lambdaops-dev-dsql-bootstrap.json
python backend/scripts/bootstrap_dsql.py roles --config /private/tmp/lambdaops-dev-dsql-bootstrap.json
python backend/scripts/bootstrap_dsql.py verify --config /private/tmp/lambdaops-dev-dsql-bootstrap.json
```

Use `--profile YOUR_OPERATOR_PROFILE` on the Python commands if a named AWS
profile is required. `schema` creates the approved three tables and waits for
their ASYNC indexes. `roles` creates separate ingest/read DB roles, grants their
table operations and maps each role to its exact Lambda IAM role ARN. `verify`
checks the committed schema, indexes, grants and mappings using a fresh
connection. No credentials or authentication tokens belong in the JSON file,
Terraform configuration or logs. If a step fails, inspect the committed objects
before retrying: earlier DDL statements may already have succeeded.

Only after `verify` succeeds, set `dsql_schema_ready = true` in `m3/dev.tfvars`,
review a fresh plan and apply it. Keep `connect_m2_alarms = false` and
`enable_http_api = false` if their prerequisites are still absent. The readiness
flag is an operator assertion; Terraform does not run or verify the SQL itself.
Bootstrap verification also does not prove that a runtime Lambda connected
successfully; record that result separately when exercising its request path.

### 2.3. Connect the three actual M2 Alarms

After M2 has been deployed and verified, set `connect_m2_alarms = true` and set
`m2_state_bucket`, `m2_state_key` and `m2_state_region` to its **existing remote
state object**. Match M2's actual `service` and `environment`. The M3 root reads
the `alarm_arns` output from that state and passes the exact three-entry map to
the module. Never invent alarm ARNs or create placeholder Alarms to bypass this
stage. The Lambda's empty Alarm allowlist rejects ingestion while disconnected.

Review and apply a new plan. With database readiness verified and
`ingestion_enabled = true` (the default), it creates/enables the EventBridge
connection and enables SQS processing. Both database readiness and M2 connection
are required; setting either one alone does not start ingestion. Keep this stage
disabled while the real M2 state and Alarms are absent.

### 2.4. Enable the authenticated read API

Obtain a real OpenID Connect issuer, audience and access token with the intended
read scope. For Cognito, follow the scope/client guidance above. Set
`jwt_issuer`, `jwt_audience` and `read_authorization_scope` to those actual values,
then set `enable_http_api = true`. With `dsql_schema_ready = true`, a reviewed
plan creates the two JWT-protected GET routes. Every route requires JWT
authentication and the configured read scope; there is no public read fallback.
This stage does not require M2 to be connected, so authenticated empty-list
reads can be checked independently. Keep the API disabled until the issuer and
token are available.

### 2.5. Verify the runtime and full event path

After the relevant stages are enabled, replay EventBridge fixtures into the dev
ingest queue with their account, region and Alarm ARN set to the real M2 values.
Check duplicate suppression, OK and INSUFFICIENT_DATA behavior, reverse
delivery, failed-message retry, both dead-letter queues and authenticated
Incident Detail. Verify that requests without a valid token or read scope are
rejected. Record the actual Lambda-to-DSQL results and HTTP results separately
from the SQL bootstrap checks.

Then repeat controlled faults to verify the whole CloudWatch → EventBridge →
SQS → Lambda → DSQL → API path. An OK event closes that Alarm episode only;
INSUFFICIENT_DATA does not close it. Local fixture tests, SQL bootstrap or a
successful Terraform apply alone do not establish AWS end-to-end completion.

If an M3 check fails, keep M2 intact and inspect the first broken hop before changing another layer. Use Terraform state and a reviewed plan for any rollback; do not remove resources manually from the AWS console.
