# LambdaOps dev Terraform roots

The `m2/` and `m3/` directories are independent Terraform roots. Deploy M2 first and M3 second. An optional `bootstrap/` root creates state storage only when no approved bucket and lock table already exist. M2 and M3 use the reusable modules under `infra/modules/`; running Terraform from `infra/` or a module directory is not the deployment path. The directory name `dev` identifies this deployment target; it does not establish the metric's `Environment` value. Set `service` and `environment` from the actual publisher and use the same values in both roots. These roots do not create an AWS account, Slash runtime, metric publisher, or identity provider.

## Before either root

1. Choose the actual dev AWS account and deployment region. Use credentials for that account; the AWS provider checks `allowed_account_ids`. Do not put credentials in Terraform files.
2. Select an approved existing S3 state bucket and DynamoDB state lock table, or complete the optional bootstrap below first. Bucket versioning and encryption must be enabled; the lock table needs a String partition key named `LockID`. The bucket/lock table must be reachable by the chosen credentials. With Terraform 1.5.7, use the DynamoDB lock table shown in the backend examples. Give bootstrap, M2, and M3 different state keys. The M3 credentials must also read the M2 state object. Reading an M2 output through `terraform_remote_state` grants access to the entire M2 state snapshot, so keep sensitive data out of that state and restrict S3 access.
3. Have the Slash service running in AWS and publishing the five `LambdaOps/Slash` metrics described by the [M2 module](../../modules/m2-alarms/README.md) every 60 seconds. In particular, enable the DB query timer in the actual runtime and confirm that the five metrics have exactly the `Service` and `Environment` values supplied to the M2 root. Measure traffic before selecting any alarm threshold or minimum count. This repository does not contain the metric publisher or a Slash deployment stack.
4. Obtain an existing OpenID Connect issuer, audience, and an access token with the intended incident read scope. Build the Python 3.12 Lambda ZIP or ZIPs with `lambdaops_m3/` and its dependencies at the ZIP root, as described by the [M3 backend](../../../backend/README.md). The ZIPs and identity provider are not created here.

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

## 1. Verify and deploy M2

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

## 2. Verify and deploy M3

Set `m3/dev.tfvars` so `m2_state_bucket`, `m2_state_key`, and `m2_state_region` identify the **deployed M2 state object**. Set `service` and `environment` to the same actual values used by M2. The M3 root reads `data.terraform_remote_state.m2.outputs.alarm_arns` and passes that exact map to the M3 module. Do not hand-copy or invent alarm ARNs. Set the M3 ZIP paths, JWT issuer, audience, and issued read scope.

From `infra/environments/dev/m3`:

```sh
terraform init -backend-config=backend.hcl
terraform validate
terraform plan -var-file=dev.tfvars -out=m3.tfplan
```

Review the plan for one incident table, EventBridge rule, three queues, two Lambdas, and two JWT-protected GET routes. Only after that review, apply the saved plan:

```sh
terraform apply m3.tfplan
terraform output
```

Replay local EventBridge fixtures into the dev ingest queue with their account, region, and ARN set to the real M2 alarm values. Check duplicate suppression, OK and INSUFFICIENT_DATA behavior, reverse delivery, failed-message retry, both dead-letter queues, and authenticated Incident Detail. Then repeat controlled faults to verify the whole CloudWatch → EventBridge → SQS → Lambda → DynamoDB → API path. Local fixture tests or a successful Terraform apply alone do not establish AWS end-to-end completion.

If an M3 check fails, keep M2 intact and inspect the first broken hop before changing another layer. Use Terraform state and a reviewed plan for any rollback; do not remove resources manually from the AWS console.
