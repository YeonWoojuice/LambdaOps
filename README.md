# LambdaOps

AI-assisted incident analysis and approval-based recovery for AWS applications.

M2 CloudWatch alarms: [Terraform module](infra/modules/m2-alarms/README.md)
(validated locally; not deployed). M3 Incident core:
[backend and fixture tests](backend/README.md) and
[EventBridge/SQS/Lambda infrastructure module](infra/modules/m3-core/README.md)
(implemented and locally tested; AWS environment verification pending).

For a real AWS deployment, follow the [dev Terraform deployment guide](infra/environments/dev/README.md).
It prepares both M2 alarms and the M3 ingest/read Lambdas; account, metric,
alarm threshold, and login provider settings must be supplied from the target
environment before applying either root.

Architecture and milestones: [LambdaOps development hub](https://app.notion.com/p/3df91c6807b1813daacceeb3d42a93dd).
