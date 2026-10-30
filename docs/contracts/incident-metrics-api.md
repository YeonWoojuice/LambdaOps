# Incident 실측 시계열 조회 API — 제안 / 사용자 검토 전

상태: **설계 초안, 사용자 검토 전. 구현·배포·실환경 검증 없음.** 이 문서는 기존 [M3 Incident 목록·상세 계약](incident-api.md)과 [Alarm 이벤트 계약](alarm-event.md)을 바꾸지 않는다. 그래프의 예시 데이터를 실제 CloudWatch 데이터로 전환하기 위한 별도 읽기 경로를 제안한다. 수치 제한과 시간 범위는 아래 **미결정 항목**을 확정해야 효력을 갖는다.

## 범위와 신뢰 경계

- 제안 경로: `GET /incidents/{incidentId}/metrics?from=<UTC>&to=<UTC>`. M3의 `GET /incidents`와 `GET /incidents/{incidentId}` 응답·`page/pageSize`는 그대로 둔다.
- API Gateway에서 실제 JWT를 검증하고, 백엔드에서 **기존 M3의 배포된 읽기 scope를 재사용하는 후보안**과 호출 주체를 재확인한다. 별도 지표 scope 분리는 사용자 결정 전이다. 브라우저는 CloudWatch API나 AWS 자격 증명을 직접 호출·보관하지 않는다. 성공 응답은 JSON과 `Cache-Control: no-store`를 사용한다.
- 서버는 요청 ID를 M3 규칙대로 대표 Incident로 해석한다. 계정·리전·서비스·환경·Alarm ARN은 **저장된 대표 Incident와 배포된 M2 설정**에서만 가져온다. 브라우저가 이 값을 쿼리로 바꿀 수 없다. 예전 ID를 요청해도 `requestedIncidentId`, 대표 `incidentId`, `redirected`를 구분한다.
- 첫 대상은 `slash-api/dev`와 [M2의 `LambdaOps/Slash` 지표 계약](../../infra/modules/m2-alarms/README.md)이다. 실제 M2 Alarm ARN·지표 발행 설정이 확인되지 않았거나 Incident의 Alarm이 그 대상이 아니면, 임의 서비스 지표를 대신 반환하지 않는다.
- 첫 운영 개요 화면도 **선택한 대표 Incident의 문맥**으로 이 API를 사용한다. 서비스 전체·여러 에피소드의 전역 대시보드 집계 API는 별도 설계하며 이 경로에서 임의 합산하지 않는다.
- 이 경로는 **Metric 집계**만 제공한다. Log 사건, 실제 계측된 Trace, GitHub 배포 정보, 원인 추론은 [M4 분석 초안](rca-analysis.md)의 별도 근거다. Metric Dimension에 요청 ID·Trace ID·배포 버전을 추가하지 않는다.

## 요청·시간·비용 경계

| 항목 | 제안 규칙 |
| --- | --- |
| `incidentId` | 기존 M3의 `inc_` + 소문자 16진수 32자. 존재하지 않으면 404. |
| `from`, `to` | 필수, 시간대가 `Z`인 RFC 3339 UTC, 소수초 최대 6자리, `from < to`. 날짜만 있는 값·중복·미지원 파라미터는 400. |
| 시간 창 | **권장 후보: 기본 60분(Alarm 시작 전 15분·후 45분), 최대 6시간**. 미래와 진행 중인 60초 구간은 조회하지 않는다. 아래 경계 규칙을 따른다. |
| 표본 수 | 기간 6시간이면 시리즈당 최대 360개의 60초 구간이다. 서버는 한 요청에서 M2 원본 지표 **정확히 5개**만 읽고 반환량·CloudWatch 호출 횟수·재시도 상한을 설정한다. 초과 요청은 400 또는 429로 거절하며 비용을 늘려 자동 재질의하지 않는다. |
| 갱신 | **초기에는 수동 갱신만 제공하는 후보안**이다. 화면 진입 시 1회, 사용자의 갱신 시 1회 조회한다. 진행 중에는 중복 요청을 막고 최신 요청의 결과만 표시한다. 자동 폴링은 별도 결정한다. |

### UTC 창과 60초 구간 — 승인 전 권장안

- `anchor = floorToMinute(detectedAt)`로 둔다. 요청은 UTC 분 경계(초·소수초 0)만 받고 자동 반올림하지 않는다. 각 구간은 `[bucketStart, bucketStart + 60초)`이고 요청도 `[from, to)`다. 6시간 상한은 `to - from <= 21600초`, 최대 360구간으로 계산한다. 분 경계가 아니면 400이다.
- 기본 창은 `from=anchor-15분`, `to=min(anchor+45분, floorToMinute(serverNow))`다. 60분은 완성된 기본 창의 길이이며 방금 발생한 Incident는 미래를 제거한 짧은 창을 반환한다. `from/to`는 필수이므로 프런트가 후보 창을 요청하되 서버가 저장된 Incident와 서버 시각으로 검증한다.
- 허용된 최초 시각은 `anchor-15분`이다. 진행 중 에피소드는 완료된 현재 분까지만, 종료된 에피소드는 `min(floorToMinute(alarmOkAt)+15분, floorToMinute(serverNow))`까지만 허용하는 후보안이다. 창은 Alarm 에피소드와 겹쳐야 하며 긴 에피소드는 최대 6시간씩 이동해서 조회한다. 기본 창에서 종료 후 15분을 넘는 부분은 이 상한으로 추가 제한한다.
- Alarm이 방금 발생해 `to=anchor`이면 사전 맥락만 조회하는 초기 예외를 허용하고 `awaiting_first_complete_bucket` 경고를 준다. 시작점에 가짜 0을 만들지 않는다. 진행 중인 현재 분은 `partial` 점으로 섞지 않고 응답 창에서 제외한다.
- 완료된 구간에도 수집 지연으로 원본이 일부만 있으면 `partial`, 전부 없으면 `no_data`다. 최신 완료 구간이 나중 조회에서 보정될 수 있음을 표시한다. `sourceTimestamp`는 실제 반환 점의 시각이며 요청 창 경계를 대신하지 않는다.

CloudWatch 조회는 Incident의 계정·리전과 같은 환경에서 `Namespace=LambdaOps/Slash`, `Service=<incident.service>`, `Environment=<incident.environment>`, `Period=60`, `Stat=Sum`으로만 한다. 계정이 다른 지표를 읽는 확장은 별도 승인 대상이다. 지표의 실제 반환 시각과 UTC 요청 범위를 응답에 남기고, 정렬·잘림·재시도로 인해 데이터가 누락됐으면 `complete`라고 하지 않는다.

## 원본 5개와 도출값 3개

| 도출 시리즈 | 필요한 60초 원본 합계 | 식·단위 |
| --- | --- | --- |
| `api5xxRate` | `ServerErrorCount`, `RequestCount` | `100 × ServerErrorCount / RequestCount`, % |
| `apiMeanLatencyMs` | `RequestDurationMsTotal`, `RequestCount` | `RequestDurationMsTotal / RequestCount`, ms. p95가 아니다. |
| `dbMeanLatencyMs` | `DBQueryDurationMsTotal`, `DBQueryCount` | `DBQueryDurationMsTotal / DBQueryCount`, ms. p95가 아니다. |

분자와 분모는 **같은 계정·리전·서비스·환경·60초 시각·요청/쿼리 모집단**이어야 한다. 서로 다른 시각의 점을 임의 보간하거나 가장 가까운 점끼리 나누지 않는다. M2의 실제 최소 요청/쿼리 수보다 표본이 적거나 분모가 0이면 수치 대신 `null`을 반환한다. M2 Alarm 수식은 이때 평가값 0을 만들지만, 화면에서 이를 건강한 0% 또는 0ms로 표현하지 않는다. 최소 건수 설정을 확인하지 못하면 임의 기본값으로 `low_volume`을 판정하지 않고 지표 소스 설정 오류로 취급한다.

화면에 Alarm 임계선·최소 건수를 표시한다면 **실제 배포된 M2 설정과 확인 시각**을 응답의 `evaluationConfig`로 제공한다. Pen·프런트 예시값을 운영 임계값처럼 재사용하지 않는다. 원본 지표 시각과 Alarm 설정 확인 시각은 서로 다른 필드다.

## 품질·빈 구간·시각

| 값 | 의미 | 화면 표현 |
| --- | --- | --- |
| `ok` | 필요한 실제 원본이 같은 60초 시각에 있고 최소 건수를 충족 | 계산한 값과 원본 시각 표시 |
| `no_data` | CloudWatch 조회 자체는 완료됐지만 해당 구간의 필요한 원본이 **모두 없음** | 결측. 0으로 채우지 않음 |
| `low_volume` | 실제 원본 합계는 있으나 확인된 M2 최소 건수 미만 또는 분모 0 | 결측 및 표본 부족 이유 표시 |
| `partial` | 필요한 원본 일부만 있거나 CloudWatch 응답이 잘림·미완료 | 결측 또는 확인된 부분만 표시하고 불완전 경고 |
| `query_failed` | 해당 원본 조회가 실패·시간 초과·권한 오류 | 데이터 없음과 구분한 오류 표시; 전부 실패하면 503 |

`bucketStart`는 조회용 60초 구간의 시작이며 **실제 이벤트 시각이 아니다**. `sourceTimestamp`는 해당 값 계산에 사용한 실제 CloudWatch 데이터 점의 시각이며 결측에서는 `null`이다. Alarm 수직 표시선은 이 API가 만든 구간 시각이 아니라 기존 상세 `stateHistory[].transitionedAt`의 **실제 EventBridge 상태 전환 시각**을 사용한다. `OK` 표시선도 서비스 전체 복구의 증거로 해석하지 않는다.

### 200 응답 예시 — 제안 형식, 실제 데이터 아님

```json
{
  "requestedIncidentId": "inc_1234567890abcdef1234567890abcdef",
  "incidentId": "inc_1234567890abcdef1234567890abcdef",
  "redirected": false,
  "scope": {
    "account": "123456789012",
    "region": "ap-northeast-2",
    "service": "slash-api",
    "environment": "dev",
    "namespace": "LambdaOps/Slash"
  },
  "window": {
    "from": "2026-10-28T01:00:00.000000Z",
    "to": "2026-10-28T01:03:00.000000Z",
    "periodSeconds": 60
  },
  "queriedAt": "2026-10-28T01:03:15.000000Z",
  "resultStatus": "complete",
  "evaluationConfig": {
    "source": "deployed_m2_alarms",
    "checkedAt": "2026-10-28T01:03:14.000000Z",
    "minimumRequestsPerPeriod": 10,
    "minimumDbQueriesPerPeriod": 10,
    "thresholds": {
      "api5xxRate": 5.0,
      "apiMeanLatencyMs": 500.0,
      "dbMeanLatencyMs": 100.0
    }
  },
  "series": [
    {
      "key": "api5xxRate",
      "unit": "%",
      "points": [
        {
          "bucketStart": "2026-10-28T01:00:00.000000Z",
          "sourceTimestamp": "2026-10-28T01:00:00.000000Z",
          "value": 5.0,
          "quality": "ok",
          "sampleCount": 20
        },
        {
          "bucketStart": "2026-10-28T01:01:00.000000Z",
          "sourceTimestamp": "2026-10-28T01:01:00.000000Z",
          "value": null,
          "quality": "low_volume",
          "sampleCount": 1
        },
        {
          "bucketStart": "2026-10-28T01:02:00.000000Z",
          "sourceTimestamp": null,
          "value": null,
          "quality": "no_data",
          "sampleCount": null
        }
      ]
    },
    {
      "key": "apiMeanLatencyMs",
      "unit": "ms",
      "points": [
        {
          "bucketStart": "2026-10-28T01:00:00.000000Z",
          "sourceTimestamp": "2026-10-28T01:00:00.000000Z",
          "value": 120.0,
          "quality": "ok",
          "sampleCount": 20
        },
        {
          "bucketStart": "2026-10-28T01:01:00.000000Z",
          "sourceTimestamp": "2026-10-28T01:01:00.000000Z",
          "value": null,
          "quality": "low_volume",
          "sampleCount": 1
        },
        {
          "bucketStart": "2026-10-28T01:02:00.000000Z",
          "sourceTimestamp": null,
          "value": null,
          "quality": "no_data",
          "sampleCount": null
        }
      ]
    },
    {
      "key": "dbMeanLatencyMs",
      "unit": "ms",
      "points": [
        {
          "bucketStart": "2026-10-28T01:00:00.000000Z",
          "sourceTimestamp": "2026-10-28T01:00:00.000000Z",
          "value": 15.0,
          "quality": "ok",
          "sampleCount": 100
        },
        {
          "bucketStart": "2026-10-28T01:01:00.000000Z",
          "sourceTimestamp": "2026-10-28T01:01:00.000000Z",
          "value": 18.0,
          "quality": "ok",
          "sampleCount": 100
        },
        {
          "bucketStart": "2026-10-28T01:02:00.000000Z",
          "sourceTimestamp": null,
          "value": null,
          "quality": "no_data",
          "sampleCount": null
        }
      ]
    }
  ],
  "warnings": []
}
```

`resultStatus=complete`는 **조회가 완결**됐다는 뜻이지 모든 구간에 값이 있다는 뜻이 아니다. 각 시리즈의 `points`는 요청 창에 속하는 60초 구간을 시간순으로 표현한다. `evaluationConfig`의 숫자는 **이 예시만을 위한 값**이며 실제 설정값이 아니다. 일부 소스만 실패하면 `resultStatus=partial`과 시리즈별 `query_failed`/ `partial` 품질·경고를 반환하고, 모든 소스가 실패하면 `503 {"code":"METRIC_QUERY_FAILED","message":"Metric query failed"}`를 반환한다.

| 조건 | 제안 오류 |
| --- | --- |
| JWT 없음·읽기 scope 없음 | 기존 M3와 같은 401·403 |
| ID 형식·UTC 범위·기간 상한·미지원 파라미터 오류 | 400 |
| 대표/연결 ID를 찾지 못함 | 404 |
| M2 소스·최소 건수 매핑 미확인 | 503 `METRIC_SOURCE_UNAVAILABLE` |
| 요청률·호출 예산 초과 | 429 `METRIC_BUDGET_EXCEEDED` |
| 모든 CloudWatch 원본 조회 실패 | 503 `METRIC_QUERY_FAILED` |

API Gateway가 먼저 거부한 401·403 응답 본문은 다를 수 있다. 내부 AWS 오류·자격 증명·원본 로그·비밀값은 브라우저 응답에 넣지 않는다.

## 검증 기준과 미결정 항목

1. 60초 원본 5개와 도출 3개의 단위·모집단·시각을 대조한다. 0 데이터, 원본 부재, 낮은 표본, 일부 원본 누락, 호출 실패를 각각 다른 화면 상태로 검증한다.
2. 다른 Incident ID의 `service/environment/account/region`을 쿼리로 덮어쓸 수 없고, 예전 ID는 대표로 연결되는지 검증한다. JWT 없음·scope 없음·허용되지 않은 대상·과한 시간 창을 거부한다.
3. UTC 분 경계·시작 포함/종료 제외·360구간 상한·현재 미완성 분 제외·새 Alarm의 초기 예외를 검증한다.
4. 실제 M2 배포 지표와 같은 창을 CloudWatch에서 읽어 API 수치·원본 시각을 대조한다. UI의 Alarm 표시는 실제 `transitionedAt`에 맞추고, 결측을 0으로 그리지 않는다. 호출 수·응답 시간·비용을 기록한다.

| 사용자 결정 | 권장 후보 / 남은 확인 |
| --- | --- |
| 기본 창·범위 | 시작 전 15분·후 45분, 종료 후 맥락 최대 15분, 최대 6시간·360구간. 최신 미완성 분 제외 |
| 갱신 | 초기 수동 갱신. 사용자별 호출률·동시성·CloudWatch 재시도/비용 상한은 실측 후 추가 결정 |
| 권한 | 기존 배포 M3 읽기 scope 재사용. 별도 scope 필요 여부 검토 |
| 지표 설정·보존 | 실제 M2 최소 건수·임계값 전달 방법, 과거 보존 기간 밖 구간의 결측 안내는 별도 결정 |

위 값은 모두 **사용자 결정 전 후보**이며 계약 확정 승인을 별도로 받는다. 이 초안은 새 API가 현재 존재한다고 주장하지 않는다.
