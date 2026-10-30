# M3 Incident 조회 API 계약

상태: **DSQL 저장·조회 코드의 목록·상세 계약** (검증 기록 2026-10-29까지). dev Terraform은 DSQL을 선택하며 클러스터와 두 Lambda를 생성했다. Cognito 보호 HTTP API를 활성화하고 실제 JWT로 빈 목록 200·무토큰 401·읽기 권한 없음 403을 확인했다. 2026-10-29에는 로컬 브라우저에서 실제 Cognito 로그인·CORS와 관리자 직접 삽입 `DEMO` Incident 1건의 목록·상세 200을 확인했다. 이 결과는 읽기 경로의 증거이며 실제 CloudWatch Alarm에 의한 자동 Incident 생성 검증은 아니다. 실제 연결 검사와 인증·프런트 검증은 [환경 검증 기록](../testing/m2-m3-verification.md)에서 구분한다. [현재·목표 아키텍처](../architecture/overview.md), [Incident DB 결정](../decisions/0001-incident-db.md).

## 적용 범위와 인증

| 항목 | 규칙 |
| --- | --- |
| 사용 주체 | 인증된 단일 프로젝트 운영자다. 사용자·프로젝트 소유권 모델과 여러 고객의 데이터 격리는 별도 설계한다. |
| 인증 경로 | API Gateway JWT authorizer가 검증한 context의 `sub` 또는 `client_id`와 읽기 scope를 Lambda에서 확인한다. 임의 Bearer 문자열을 직접 신뢰하지 않는다. |
| 읽기 권한 | 기본 `incidents:read`다. `REQUIRED_READ_SCOPE`로 발급자의 실제 scope에 맞출 수 있다. 권한이 있는 운영자는 이 프로젝트의 전체 Incident를 조회한다. |
| 성공 응답 | JSON, `Cache-Control: no-store`를 사용한다. 시각은 마이크로초를 포함한 UTC 문자열로 반환한다. |
| DSQL 선택 | `INCIDENT_STORE=dsql`일 때 아래 페이지 번호 계약을 사용한다. DB 연결·권한·스키마 준비가 필요하며 코드 추가만으로 배포가 전환되지는 않는다. |
| 기존 호환 경로 | DynamoDB를 명시하거나 Terraform 밖에서 저장 방식을 생략한 Python 호출은 문서 끝의 `limit`·`nextToken` 계약을 유지한다. 실제 dev Lambda는 DSQL을 명시한다. 프런트와 API의 두 형식을 섞지 않는다. |

권한 검사는 목록과 상세 모두에 적용한다. JWT 읽기 scope는 사용자별 소유권 필터와 다르다. 현재 범위를 다중 사용자 데이터 격리가 구현된 것으로 표시하지 않는다.

## DSQL 목록 — `GET /incidents`

### 요청

모든 쿼리 파라미터는 선택 사항이며 단일 값을 받는다. 생략한 필터는 전체를 뜻한다. 빈 문자열이나 지원하지 않는 파라미터는 `400`으로 거부한다.

| 파라미터 | 형식·기본값 | 의미 |
| --- | --- | --- |
| `page` | 정수 `1~1,000,000`, 기본 `1` | 보고 싶은 페이지 번호다. 상한은 비정상적으로 큰 입력을 제한하기 위한 API 경계이며 빠른 응답을 보장하는 범위가 아니다. |
| `pageSize` | 정수 `1~100`, 기본 `50` | 한 페이지에 담을 최대 장애 기록 수다. |
| `service` | 문자열, 최대 100자 | 서비스 이름이 정확히 같은 기록만 조회한다. 대소문자를 구분한다. |
| `environment` | 문자열, 최대 32자 | 환경 이름이 정확히 같은 기록만 조회한다. 대소문자를 구분한다. |
| `alarmState` | `ALARM`, `OK`, `INSUFFICIENT_DATA` | 해당 에피소드의 현재 Alarm 상태를 필터링한다. |
| `incidentState` | `NEW` | Incident 업무 상태를 필터링한다. M3는 `NEW`만 지원하며 `Resolved`는 허용하지 않는다. |
| `from` | 시간대가 있는 RFC 3339 시각 | `detectedAt >= from`이다. 이 시각부터 포함한다. |
| `to` | 시간대가 있는 RFC 3339 시각 | `detectedAt < to`이다. 이 시각은 제외한다. 두 시각을 지정하면 `from < to`여야 한다. |
| `search` | 비어 있지 않은 문자열, 최대 200자 | Incident ID·Alarm 이름·서비스·환경 중 문자열을 포함한 기록을 찾는다. 대소문자를 구분하지 않는다. 원본 JSON은 검색하지 않는다. |
| `sortBy` | `detectedAt`·`lastStateTransitionAt`·`createdAt`, 기본 `detectedAt` | 발생 시각·마지막 상태전환시각·기록 생성 시각 중 정렬 기준을 선택한다. |
| `sortOrder` | `asc`·`desc`, 기본 `desc` | 오래된 순 또는 최신순이다. 같은 정렬값에서는 Incident ID를 같은 방향으로 정렬한다. |

날짜만 있는 값이나 시간대가 없는 값은 받지 않는다. 예를 들어 하루 조회는 `from=2026-10-27T15:00:00Z&to=2026-10-28T15:00:00Z`로 한국 시각 10월 28일을 표현할 수 있다. `+09:00`을 URL에 사용할 때에는 `+`를 `%2B`로 인코딩한다. 소수초는 최대 6자리다.

조건은 모두 AND로 결합한다. 검색 대상 네 필드는 서로 OR로 결합한다. 검색어의 `%`, `_`, `!`는 와일드카드가 아닌 입력 문자 그대로 검색한다. 서비스·환경·검색어의 앞뒤 공백은 자동 제거하지 않는다.

```http
GET /incidents?page=2&pageSize=20&service=slash-api&environment=dev&alarmState=ALARM&sortBy=detectedAt&sortOrder=desc
```

### 성공 응답

```json
{
  "items": [
    {
      "incidentId": "inc_1234567890abcdef1234567890abcdef",
      "account": "123456789012",
      "region": "ap-northeast-2",
      "service": "slash-api",
      "environment": "dev",
      "alarmArn": "arn:aws:cloudwatch:ap-northeast-2:123456789012:alarm:slash-api-5xx",
      "alarmName": "slash-api-5xx",
      "detectedAt": "2026-10-28T01:00:00.000000Z",
      "alarmState": "ALARM",
      "incidentState": "NEW",
      "lastStateTransitionAt": "2026-10-28T01:00:00.000000Z",
      "originalEventRef": "eventbridge-example-1",
      "createdAt": "2026-10-28T01:00:02.000000Z",
      "updatedAt": "2026-10-28T01:00:02.000000Z"
    }
  ],
  "page": 1,
  "pageSize": 20,
  "totalItems": 1,
  "totalPages": 1,
  "hasNextPage": false
}
```

위 JSON은 필터 결과가 한 건인 첫 페이지 예시다. 실제 계정이나 운영 이벤트가 아니다.

| 필드 | 의미 |
| --- | --- |
| `items` | 대표 Incident의 요약 목록이다. DB 행을 그대로 노출하는 것이 아니라 Alarm·Incident·대표 근거에서 필요한 필드를 조합한다. |
| `incidentId` | 최초 발급한 고정 ID다. 발생 시각이 보정돼도 재발급하지 않는다. |
| `detectedAt` | 에피소드의 ALARM 시작 시각이다. 이전 상태 근거에 따라 대표 원본 이벤트보다 앞설 수 있다. |
| `alarmState` | 해당 Alarm 에피소드의 최신 상태다. `OK`는 해당 에피소드가 끝났다는 뜻이다. |
| `incidentState` | 조사·대응 업무 상태다. M3에서는 `NEW`이며 `alarmState=OK`가 자동 해결을 뜻하지 않는다. |
| `originalEventRef` | 대표 ALARM 근거의 EventBridge 이벤트 ID다. 내부 DB 중복 키나 페이징 토큰이 아니다. |
| `alarmOkAt` | OK 종료 시각이 있는 경우에만 포함한다. 서비스 전체 복구 시각은 아니다. |
| `createdAt`·`updatedAt` | DB 기록 생성·마지막 보정 시각이다. 장애 발생·상태전환시각과 구분한다. |
| `totalItems` | 같은 필터·검색·권한 조건을 만족하는 대표 Incident의 정확한 건수다. |
| `totalPages` | `totalItems / pageSize`를 올림한 페이지 수다. 결과가 없으면 `0`이다. |
| `hasNextPage` | 요청한 번호 다음에 페이지가 존재하는지 나타낸다. |

목록·검색·전체 건수에는 `redirect_to_incident_id IS NULL`인 대표만 포함한다. 기존 ID 보존용 연결 행은 장애 건수에 더하지 않는다. 목록에는 `rawEvent`와 `stateHistory`를 포함하지 않는다.

결과가 없거나 마지막 페이지를 넘겨도 `200`과 빈 `items`를 반환한다. 요청한 `page`는 유지하며 실제 `totalItems`·`totalPages`와 `hasNextPage=false`를 반환한다.

한 응답의 목록과 건수는 같은 짧은 읽기 트랜잭션에서 조회한다. 다만 다음 페이지를 별도로 요청하는 사이 신규 장애가 들어오거나 시작 시각이 보정되면 페이지 위치가 달라질 수 있다. 페이지 이동 동안 DB 트랜잭션을 열어 두지 않는다. 포함 검색·정확한 건수·깊은 페이지는 데이터가 늘면 처리량이 증가할 수 있으므로 실제 DSQL에서 실행 계획·응답 시간·사용량을 측정한다. [AWS 페이지네이션 패턴](https://aws.amazon.com/blogs/database/pagination-patterns-in-amazon-aurora-dsql/).

## DSQL 상세 — `GET /incidents/{incidentId}`

`incidentId`는 `inc_` 뒤에 소문자 16진수 32자다. 조회 성공은 `200`이며, 목록 요약에 다음 필드를 추가한다.

| 필드 | 의미 |
| --- | --- |
| `requestedIncidentId` | 클라이언트가 요청한 ID다. |
| `incidentId` | 현재 대표 Incident의 ID다. |
| `redirected` | 요청 ID가 대표 ID와 다르면 `true`다. HTTP 리다이렉트를 뜻하지 않는다. |
| `stateHistory` | 해당 에피소드에 속하는 실제 수신 이벤트의 시간순 목록이다. 각 항목은 `eventId`, `state`, `transitionedAt`, `rawEvent`를 담는다. |

같은 에피소드의 기존 ID가 연결 행이 됐어도 `200`으로 대표 상세를 반환한다. 예를 들어 I2가 I1에 연결되면 I2 요청에 `requestedIncidentId=I2`, `incidentId=I1`, `redirected=true`를 반환한다. 프런트는 대표 ID로 후속 이동을 구성할 수 있다. 없는 ID는 `404`다.

요약·ID 연결·이력은 같은 읽기 시점에 조회한다. 에피소드 구간의 정규화 근거를 확인하고 필요한 실제 이벤트의 원본만 읽는다. 이전 상태에서만 추론한 전환을 가짜 원본 이벤트로 생성하지 않으므로, 요약의 시작 시각과 첫 실제 이력 시각이 다를 수 있다. AI 분석·승인·복구 결과는 M3 응답에 포함하지 않는다.

## 오류 응답

| 조건 | 응답 |
| --- | --- |
| JWT context 또는 식별 정보 없음 | `401 {"message":"Unauthorized"}` |
| 필요한 읽기 scope 없음 | `403 {"message":"Forbidden"}` |
| 지원하지 않는 쿼리 파라미터 | `400 {"message":"Unsupported query parameter"}` |
| 유효하지 않은 파라미터 값 | `400 {"message":"Invalid page"}` 등 해당 파라미터 이름을 포함한다. |
| `from >= to` | `400 {"message":"Invalid time range"}` |
| 잘못된 ID 형식 | `400 {"message":"Invalid incidentId"}` |
| 존재하지 않는 Incident | `404 {"message":"Incident not found"}` |
| DB 연결·조회 실패 또는 저장된 연결·근거의 모순 | `503 {"message":"Incident store unavailable"}`. 내부 SQL·접속 정보·원본 데이터는 노출하지 않는다. |

API Gateway가 Lambda 호출 전에 거부한 요청은 본문이 다를 수 있다. 인증된 사용자에게 원본 Alarm 이벤트를 제공하는 것은 현재 단일 프로젝트 운영자 범위의 계약이다. 다른 사용자에게 데이터 일부를 공유하는 기능은 별도 권한 모델이 필요하다.

## 기존 DynamoDB 모드의 호환 계약

기존 저장소·프런트가 사용하는 형식은 DSQL의 페이지 번호 형식과 다르다. `INCIDENT_STORE`를 설정하지 않았거나 `dynamodb`인 동안에는 다음 계약을 유지한다.

| 항목 | 기존 동작 |
| --- | --- |
| 목록 파라미터 | `limit`: 기본 50, 정수 1~100. `nextToken`: 이전 응답의 불투명한 다음 위치 토큰 |
| 목록 응답 | `{ "items": [...], "nextToken": null 또는 문자열 }` |
| 목록 정렬 | 발생 시각 최신순. DynamoDB 조회 인덱스의 변경 반영이 늦을 수 있다. |
| 상세 응답 | 기존 요약과 `stateHistory`다. DynamoDB의 일관된 읽기를 사용한다. DSQL의 `requestedIncidentId`·`redirected` 계약은 적용하지 않는다. |
| 지원 범위 | 검색·서비스/환경/상태/기간 필터·페이지 번호·정확한 건수는 이 호환 경로에서 제공하지 않는다. |

DSQL 모드에 기존 `limit`·`nextToken`을 보내면 `400`으로 거부한다. 서로 다른 페이징 방식을 조용히 섞지 않는다. DSQL 인프라 생성과 프런트 호출 형식 변경, 실제 JWT·HTTP 검증, 기존 데이터 이관은 별도 작업이다.

## 구현·검증 근거

- 요청 검증: [query_contract.py](../../backend/lambdaops_m3/query_contract.py), [파라미터 테스트](../../backend/tests/test_query_contract.py).
- 인증·HTTP 분기: [read_handler.py](../../backend/lambdaops_m3/read_handler.py).
- SQL 조회: [dsql_queries.py](../../backend/lambdaops_m3/dsql_queries.py), [승인된 생성 SQL](../../backend/sql/dsql/README.md).
- 실제 DSQL·JWT·Lambda 검증 상태: [M2·M3 환경 검증](../testing/m2-m3-verification.md).
- 기존 [Notion HTTP API 보드](https://app.notion.com/p/3fa91c6807b180b4bf2aef0a52d2b247)는 작업 상태와 이 계약의 링크를 관리한다. 새 보드나 중복 문서는 만들지 않는다.
