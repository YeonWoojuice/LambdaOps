# M3 CloudWatch Alarm 이벤트 계약

상태: **코드·fixture 테스트 있음, 실제 AWS 전달 미검증** (2026-10-28). 이 문서는 현재 M3의 계약이며 서비스 단위 장애 병합 규칙을 새로 만드는 문서가 아닙니다. 전체 흐름은 [아키텍처](../architecture/overview.md)를 봅니다.

## 받는 이벤트

수집 Lambda는 SQS 레코드를 받고, 각 레코드의 `body`를 EventBridge JSON으로 해석합니다. [현재 파서](../../backend/lambdaops_m3/contracts.py)는 다음을 확인합니다.

| 필드 | 현재 필수 조건 |
| --- | --- |
| `source`, `detail-type` | 각각 `aws.cloudwatch`, `CloudWatch Alarm State Change` |
| `id`, `account`, `region` | 비어 있지 않은 EventBridge ID, AWS 계정, 리전 |
| `resources` | CloudWatch Alarm ARN 정확히 하나. ARN의 계정·리전은 이벤트와 같고, M2에서 설정한 Alarm 3종의 허용 목록에 있어야 함 |
| `detail.alarmName` | ARN에 적힌 Alarm 이름과 같아야 함 |
| `detail.state.value` | `ALARM`, `OK`, `INSUFFICIENT_DATA` 중 하나 |
| `detail.state.timestamp` | 시간대가 있는 시각. UTC와 마이크로초 정밀도로 정규화 |
| `detail.previousState.value`, `.timestamp` | **현재 파서에서는 둘 다 필수**. 이전 상태는 같은 세 값 중 하나이며, 이전 전환 시각은 새 전환보다 빨라야 함 |

`service`와 `environment`는 SQS 본문이 아니라 Lambda 설정에서 가져옵니다. EventBridge `id`는 원본 이벤트를 찾기 위한 참조로 보관합니다.

## 중복과 Alarm 에피소드

동일 전환 판단 키는 **계정·리전·Alarm ARN·새 상태·정규화한 전환 시각**을 순서대로 넣은 SHA-256 값입니다. SQS 메시지 ID, 전달 시각, EventBridge 이벤트 ID는 이 키에 넣지 않습니다. 이 **전환 중복 키**와 한 **Alarm 에피소드의 Incident ID**는 서로 다른 개념입니다. [계약 코드](../../backend/lambdaops_m3/contracts.py)와 [상태 투영 코드](../../backend/lambdaops_m3/projection.py)가 기준입니다.

- 한 Alarm 에피소드의 `ALARM` 전환을 기준으로 대표 Incident 한 건을 만듭니다. 동일 전환의 재전달은 새 Incident를 만들지 않습니다. `INSUFFICIENT_DATA` 뒤의 재알람은 같은 에피소드이며, `OK` 뒤의 새 알람은 새 에피소드입니다.
- 이벤트가 도착한 순서가 전환 순서일 필요는 없습니다. `OK` 등이 먼저 도착하면 `previousState` 연결로 에피소드 관계를 재구성할 수 있습니다. 다만 실제 `ALARM` 이벤트가 아직 수신되지 않았다면 Incident를 저장하지 않고, 뒤늦게 그 이벤트가 도착한 뒤 한 건을 반영합니다.
- `OK`는 그 에피소드에 `alarmState=OK`와 `alarmOkAt`을 기록합니다. 서비스 전체 복구의 증명이나 `incidentState=Resolved` 전환은 아닙니다.
- `INSUFFICIENT_DATA`는 상태 이력에 반영하지만 에피소드를 종료하거나 `alarmOkAt`을 설정하지 않습니다.
- 이전 상태 연결과 전환 시각으로 유효한 역순 전달을 재구성합니다. 오래된 이벤트가 최신 상태를 덮어쓰면 안 됩니다. 모순된 전환 근거는 기존 Incident를 조용히 바꾸는 대신 처리 실패로 돌립니다.
- 현재 DynamoDB 어댑터는 조건부 트랜잭션과 Alarm별 버전으로 원본 이벤트와 Incident 반영을 함께 처리합니다. DSQL 전환에서도 외부에 보이는 위 규칙을 보존해야 합니다. [DB 결정](../decisions/0001-incident-db.md).

이 문서는 **현재 DynamoDB 코드의 이벤트 처리 계약**입니다. 승인된 DSQL 논리 설계에서는 최초 발급한 Incident ID를 고정하고 같은 Alarm·에피소드의 예전 ID를 연결 행으로 보존합니다. 이 방식의 물리 스키마·코드·상세 HTTP 계약은 아직 구현되지 않았습니다. [DB 결정](../decisions/0001-incident-db.md).

## 실패와 검증 범위

[수집 핸들러](../../backend/lambdaops_m3/ingest_handler.py)는 SQS 레코드를 각각 처리하고 실패한 `messageId`만 `batchItemFailures`로 반환합니다. Terraform에는 EventBridge 전달 실패 큐와 SQS 소비 실패 큐가 정의돼 있습니다. 큐에 들어간 사실과 Incident 저장 완료는 구별해야 합니다.

[Fixture](../../backend/tests/fixtures/)와 [테스트](../../backend/tests/)가 `ALARM`, `OK`, `INSUFFICIENT_DATA`, 중복, 역순을 다룹니다. [CI](https://github.com/YeonWoojuice/LambdaOps/actions/runs/38544496325)는 코드·설정 검사를 통과했지만, 실제 Alarm 전환·큐 전달·AWS 트랜잭션·두 실패 경로는 [환경 검증](../testing/m2-m3-verification.md)이 남아 있습니다. 기존 [Notion 이벤트 처리 명세](https://app.notion.com/p/3fa91c6807b1808e8c2dcd6973fd011f)는 작업·검토 이력을 연결하는 위치로 유지합니다.
