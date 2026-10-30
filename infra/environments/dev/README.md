# LambdaOps dev Terraform roots

## Deployment scope and current state — 2026-10-28

Aurora DSQL is the selected Incident database. The first M3 apply created 14
resources in account `642935793048`, Seoul (`ap-northeast-2`), including the
dedicated DSQL cluster `krugaq2rx2w52ku2bbp7e6piw4`, runtime IAM roles, queues and
two Lambdas. The approved schema and separate SQL roles/IAM mappings now pass
live verification. The read Lambda passes six direct-invocation checks, including
empty list/filter queries against DSQL. `dsql_schema_ready=true` records DB
readiness. A subsequent apply enabled the Cognito-protected HTTP API: eight
resources created, one read-Lambda scope setting updated, none deleted.
`enable_http_api=true`; real code+PKCE tokens now pass empty-list 200, missing-token
401 and missing-read-scope 403 checks. `connect_m2_alarms=false` remains unchanged:
there is no M2 state, deployed M2 Alarm or EventBridge rule, and SQS processing
is disabled. Actual ingestion and an existing Incident detail 200 remain
unverified. The follow-up plan reports no changes. Pass `-var-file=dev.tfvars`
explicitly for this root; that filename is not auto-loaded by Terraform. See the
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

For the local LambdaOps operator browser, set
`http_api_cors_allowed_origin = "http://localhost:8765"` in the reviewed M3
environment inputs. The API accepts that exact origin only for GET with the
`authorization` request header and handles OPTIONS preflight without a JWT;
actual GET requests still require the configured JWT scope. The default is no
CORS. This input is a public origin, not a token or client secret. Verify the
browser's preflight and real list/detail responses after applying the reviewed
plan; do not infer browser access from a direct CLI request.

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

## Slash 시험 환경의 배포 방식과 비용 — 2026-10-28

현재 권장 검토안은 **이미 작성된 Fargate 개인 실험 구성을 사용하고, 시험 후 experiment 자원을 삭제하는 방식**이다. 운영체제 관리와 새 배포 코드 작성 부담을 줄일 수 있다. 장시간 반복 개발이 필요하면 EC2 한 대 구성을 비교한다. 이 절은 배포 결정·실제 청구 검증이 아니며, 이번 조사에서는 Slash 코드와 AWS 자원을 변경하지 않았다.

### 확인한 기존 구조

별도 `slash-personal` 저장소의 `codex/m2-cloudwatch-metrics` 브랜치, 커밋 `d3bab1c`와 미커밋 개인 인프라 초안을 읽었다. 근거는 해당 저장소의 `infra/personal/README.md`, `infra/personal/experiment/main.tf.json`, `observability/personal/README.md`, `docs/observability/M2_CLOUDWATCH.md`다. 미커밋 코드를 배포 완료로 간주하지 않는다.

| 구성 | 기존 개인 실험안 | 역할 |
| --- | --- | --- |
| API | Java 21·Spring Boot 3.5.9, Fargate 1 vCPU·3 GiB 1개 | 시험할 Slash 업무 서버 |
| 관측 | Prometheus·Grafana, Fargate 1 vCPU·2 GiB 1개 | 지표 수집과 그래프 |
| 업무 DB | RDS PostgreSQL db.t4g.small, Single-AZ, gp3 20 GB | Slash 업무 데이터. LambdaOps Incident 저장소인 DSQL과 별개 |
| 캐시 | Valkey cache.t4g.micro 1개 | 기존 Slash의 캐시 의존성 |
| 진입점 | 2 AZ ALB, 개인 Cognito·도메인·ACM 인증서 | 시험자 접근과 인증 |
| 네트워크 | ALB 주소 최소 2개 + task 공인 IP 2개, DB·캐시는 private | NAT Gateway·유료 VPC endpoint 없음 |
| 수명 | foundation은 재사용, experiment는 필요할 때 생성 후 삭제 | foundation은 ECR·개인 Cognito, experiment는 실행 자원·시험 데이터 |

기존 결정은 1~2시간 시험 후 결과를 보관하고 실험 자원을 삭제하는 방식이다. DB 백업·최종 snapshot을 보존하지 않는 합성 데이터용 구성이다. `expires_at`은 표시용 태그이며 자동 삭제를 실행하지 않는다. LambdaOps의 수집·조회 Lambda를 사용하기 위해 Slash API까지 Lambda로 옮길 필요는 없다.

### 서울 리전 비용 비교

2026-10-28 AWS Price List Query API의 서울 On-Demand 가격을 조회했다. **USD·세전·무료 크레딧과 할인 미반영**, 월 비교는 730시간이다. 단가·SKU·가정은 [가격 근거 JSON](../../../docs/testing/evidence/2026-10-28-slash-cost-estimate.json)에 보관한다.

| 기존 Fargate 실험안의 주요 자원 | 시간당 산식 | USD/시간 |
| --- | --- | ---: |
| API와 관측 CPU | 2 vCPU × 0.04656 | 0.09312 |
| API와 관측 메모리 | 5 GiB × 0.00511 | 0.02555 |
| RDS db.t4g.small | 1 × 0.051 | 0.05100 |
| Valkey cache.t4g.micro | 1 × 0.0192 | 0.01920 |
| ALB 기본료 | 1 × 0.0225 | 0.02250 |
| 공인 IPv4 최소 4개 | 4 × 0.005 | 0.02000 |
| **주요 실행비 합계** | 위 항목 합산 | **0.23137** |

실행비 단가는 [Fargate](https://aws.amazon.com/fargate/pricing/), [RDS PostgreSQL](https://aws.amazon.com/rds/postgresql/pricing/), [ElastiCache](https://aws.amazon.com/elasticache/pricing/), [ALB](https://aws.amazon.com/elasticloadbalancing/pricing/), [IPv4](https://aws.amazon.com/vpc/pricing/)의 서울 지역 가격을 사용한다. ALB 처리량은 추가로 0.008 USD/LCU·시간이다. 위 합계는 저장소·관측·인증 등 모든 비용의 총액이 아니다.

| 배포안 | 주요 실행비 | 730시간 유지 시 비교 예산 | 선택의 대가 |
| --- | ---: | ---: | --- |
| **기존 Fargate + RDS + Valkey** | 약 $0.231/시간 | **약 $176.61~182.45/월** | 기존 초안 재사용, 서버 운영 부담이 적음. 상시 유지 비용은 더 큼 |
| EC2 t3.large 8 GiB + Docker Compose | IP 포함 $0.109/시간 | **약 $85.60/월** | API·DB·캐시·관측을 한 서버에서 운영. 새 배포 코드·패치·복구 절차와 자원 경합 검증 필요 |
| EC2 t4g.large 8 GiB + Docker Compose | IP 포함 $0.0882/시간 | **약 $70.41/월** | ARM 이미지 호환 검증이 추가로 필요 |

EC2 크기는 초기 비교 가정이다. 현재 API·관측 task의 메모리 합만 5 GiB이므로 모든 구성요소를 유지하는 비교에 2~4 GiB를 충분한 크기로 제시하지 않았다. 8 GiB도 실제 사용량 측정 전이며 성능을 보장하지 않는다. [EC2 On-Demand](https://aws.amazon.com/ec2/pricing/on-demand/), [EBS](https://aws.amazon.com/ebs/pricing/).

월 비교의 공통 가정은 custom metric 6개 $1.80, Alarm에서 참조하는 metric 합계 6개 $0.60, 로그 수집 1 GB와 평균 보관 1 GB $0.7914, ECR 이미지 총 1 GB $0.10이다. 기존 publisher는 필수 5개 외에 Hikari 값이 있을 때 DBConnectionsActive를 발행하므로 최대 6개를 기준으로 보수적으로 계산한다. 각 Alarm은 두 metric을 사용하는 수식이어서 세 Alarm의 비용을 단순히 3개 × $0.10으로 계산하지 않는다. [CloudWatch 과금 기준](https://aws.amazon.com/cloudwatch/pricing/), [ECR](https://aws.amazon.com/ecr/pricing/).

Fargate 월 비교에는 RDS 저장소 $2.62, secret 3개 $1.20, Cloud Map 자원 1개 $0.10, private DNS zone 1개 $0.50을 더했다. ALB 처리량을 평균 0~1 LCU로 가정한 범위이며, 높은 트래픽의 상한이 아니다. EC2 비교에는 EBS gp3 30 GB $2.736을 더했고 별도 ALB·RDS·ElastiCache·Cloud Map을 두지 않는다. EC2에서 secret 3개를 따로 쓰면 $1.20/월 등이 추가되므로 동일한 운영 기능을 모두 보장하는 비교는 아니다.

도메인 등록·public DNS, Cognito/SMS, 네트워크 전송·API 요청, CPU credit 초과료와 기존 LambdaOps DSQL·Lambda·큐 비용은 위 비교에서 별도다. 같은 비용을 전체 프로젝트 예산에 두 번 더하지 않도록 구분한다. 무료 혜택과 실제 청구 계정의 잔여 크레딧은 조사하지 않았다.

### 단기 시험 예산과 종료 기준

- 기존안의 주요 자원을 정확히 2시간 유지하면 실행비는 약 **$0.46**다. 실제 생성·시작·삭제 시간과 저장·로그 비용을 포함한 총 청구액은 아니다.
- 생성부터 삭제까지 총 4~6시간이라고 가정하면 주요 실행비는 약 **$0.93~1.39**다. 작은 로그·처리량을 전제로 **시험 1회 $1~3의 예산 여유**를 제안하며 상한 보장은 아니다. 별도 유지하는 이미지·도메인·인증 등의 비용은 남는다.
- custom metric은 데이터를 보낸 시간, Alarm은 존재한 시간에 따라 월 요금이 비례 계산된다. 위 730시간 비교액이 짧은 시험에 그대로 부과되지는 않는다.
- private DNS zone은 생성 후 12시간 안에 삭제하면 zone 요금이 없다. 그 이후에는 $0.50이며 월할하지 않는다. 반복 생성한 zone마다 이 기준이 적용된다. [Route 53](https://aws.amazon.com/route53/pricing/).
- task 수를 0으로 만들어도 ALB·Valkey 등은 남는다. 기존 실험안에서는 결과를 보관한 뒤 experiment 삭제와 잔여 자원 확인까지 끝내야 한다. RDS 중지는 저장소 비용을 없애지 않고 7일 후 자동 재시작한다. [RDS 중지 동작](https://docs.aws.amazon.com/AmazonRDS/latest/UserGuide/USER_StopInstance.html).

### M2에 연결하기 전에 할 작업

1. 기존 개인 초안의 계정·이미지·Cognito·개인 도메인·인증서 입력과 비용을 정리한다. Slash 로그인과 LambdaOps 조회 인증의 사용자·scope는 각각 확인한다.
2. `personal` 프로필에서 꺼진 CloudWatch 발행을 켜고 task 역할에 해당 namespace의 PutMetricData 권한을 추가한다. 기존 publisher 코드를 재사용한다.
3. CloudWatch 차원을 `Service=slash-api`, `Environment=dev`로 M2·M3와 일치시킨다. 기존 Prometheus의 personal 태그와 구분한다.
4. 실제 배포 환경의 정상 지표를 측정해 Alarm 임계값·최소 표본 수를 정한다. 2026-10-02 로컬 Slash의 AWS 지표 발행 기록은 수식 검증이며 AWS 배포 환경의 임계값 근거가 아니다.
5. 내부 장애 시험 방법을 설계한다. 현재 personal 프로필은 local/m1과 함께 켤 수 없고 공개 ALB도 장애 주입 경로를 허용하지 않는다. 이 제한을 우회하지 않고 필요한 시험 경로를 검토한다.
6. 검토한 계획으로 시험 환경을 배포하고 실제 Alarm ARN을 LambdaOps에 연결한다. ALARM·OK·수집·상세 조회 근거를 보관한 뒤 실험 자원을 정리한다.
