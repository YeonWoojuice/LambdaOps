# M3 Incident 조회 API 계약

상태: **현재 DynamoDB 코드 기준으로 구현·자동 테스트 존재, 실제 AWS/JWT 연동 미검증** (2026-10-28). DSQL 전환 후의 새 목록 조회 계약은 아직 설계·구현되지 않았습니다. [목표와 현행 아키텍처](../architecture/overview.md).

## 공통 인증과 응답

[Terraform](../../infra/modules/m3-core/main.tf)은 아래 두 GET 경로에 API Gateway JWT authorizer와 읽기 scope를 설정합니다. [조회 Lambda](../../backend/lambdaops_m3/read_handler.py)는 API Gateway가 검증해 넘긴 JWT context에서 `sub` 또는 `client_id`, 그리고 필요한 scope를 다시 확인합니다. 임의의 Bearer 문자열을 Lambda가 직접 신뢰하지 않습니다. 기본 scope는 `incidents:read`이며 환경 설정으로 바꿀 수 있습니다. 실제 발급자, audience, 발급된 토큰으로의 검증은 남아 있습니다.

**현재는 읽기 scope를 가진 호출자가 전체 Incident 목록과 상세를 조회합니다. 사용자별 소유권 필터는 구현되지 않았습니다.** 누가 읽을 수 있는지와 권한 범위는 실제 인증 연동 전에 확정해야 합니다. 성공 응답은 JSON이며 `Cache-Control: no-store`를 사용합니다.

## `GET /incidents`

| 항목 | 현재 계약 |
| --- | --- |
| `limit` | 선택. 기본 `50`, 정수 `1~100` |
| `nextToken` | 선택. 이전 응답의 불투명한 다음 페이지 토큰. 클라이언트는 해석·수정하지 않음 |
| `200` 본문 | `{ "items": [...], "nextToken": null 또는 문자열 }` |
| 정렬·일관성 | `detectedAt` 최신순. DynamoDB 조회용 인덱스에서 읽으므로 AWS에서는 새 변경의 목록 반영이 늦을 수 있음 |
| 빈 목록 | `200`과 `{ "items": [], "nextToken": null }` |

`items`의 각 요약에는 `incidentId`, `account`, `region`, `service`, `environment`, `alarmArn`, `alarmName`, `detectedAt`, `alarmState`, `incidentState`, `lastStateTransitionAt`, `originalEventRef`가 있습니다. `OK` 전환이 있는 경우에만 `alarmOkAt`을 포함합니다. 목록에는 원본 이벤트와 `stateHistory`를 포함하지 않습니다. 현재 `incidentState`는 `NEW`이며 `alarmState=OK`여도 자동으로 `Resolved`가 되지 않습니다. [저장소 코드](../../backend/lambdaops_m3/store.py).

## `GET /incidents/{incidentId}`

`incidentId`는 현재 `inc_` 뒤에 32자리 소문자 16진수가 오는 형식입니다. `200` 본문은 위 요약 필드와 `stateHistory`를 포함합니다. 각 이력 항목은 `eventId`, `state`, `transitionedAt`, 원본 EventBridge JSON인 `rawEvent`를 담습니다. 상세 조회는 현재 DynamoDB의 일관된 읽기를 사용합니다. AI 분석·승인·복구 결과는 아직 포함하지 않습니다.

## 오류와 미구현 범위

| 조건 | 현재 조회 Lambda의 응답 |
| --- | --- |
| JWT context 또는 식별 정보 없음 | `401 {"message":"Unauthorized"}` |
| 필요한 읽기 scope 없음 | `403 {"message":"Forbidden"}` |
| 잘못된 `limit` 또는 `nextToken` | `400`과 각각 `Invalid limit`, `Invalid nextToken` |
| 잘못된 `incidentId` | `400 {"message":"Invalid incidentId"}` |
| 없는 Incident | `404 {"message":"Incident not found"}` |

API Gateway가 Lambda 호출 전에 거부한 요청은 오류 본문이 다를 수 있습니다. 서비스·환경·상태·기간 필터, 검색, 페이지 번호, 정확한 전체 건수, 사용자별 조회 제한은 **현재 계약에 없습니다**. 이것들은 [DSQL 결정](../decisions/0001-incident-db.md)에 따른 별도 계약 설계와 구현이 필요합니다. 승인·복구 API도 M3의 이 문서에 추가하지 않습니다.

[테스트](../../backend/tests/test_incidents.py)와 [CI](https://github.com/YeonWoojuice/LambdaOps/actions/runs/38544496325)는 현재 핸들러와 로컬 대역을 확인합니다. 실제 토큰·HTTP 응답·DynamoDB 조회는 [환경 검증 기준](../testing/m2-m3-verification.md)에 남아 있습니다. 기존 [Notion HTTP API 보드](https://app.notion.com/p/3fa91c6807b180b4bf2aef0a52d2b247)는 작업 상태와 이 계약의 링크를 관리하는 위치입니다.
