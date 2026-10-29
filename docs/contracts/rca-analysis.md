# M4 읽기 전용 MCP·RCA 보고서 계약 — 제안 / 사용자 검토 전

상태: **설계 초안, 사용자 검토 전. M4 Agent·MCP 도구·분석 API·저장 구조는 아직 구현·배포되지 않았다.** 사용자가 확정한 [최종 아키텍처](../architecture/overview.md)의 Lambda Backend → AgentCore의 Strands Agent → Bedrock 모델 API·읽기 전용 MCP 도구 흐름을 전제로 한다. M3의 [Alarm 에피소드](alarm-event.md)와 [Incident 조회 계약](incident-api.md)은 변경하지 않는다. 이 문서는 M5 Action Tool·승인 권한을 부여하지 않는다.

## 첫 범위와 실행 조건

- 첫 검증 대상은 **`slash-api/dev`의 주입한 장애 시나리오 한 개**다. 앞서 논의한 제한된 API 500 오류를 첫 후보로 두되 주입 방식·정답 근거는 사용자 검토 후 확정한다. 실제 M2→M3 수집으로 생성된 대표 Incident에만 분석을 연결하는 것을 기본 제안으로 한다. 직접 넣은 `DEMO` 시드나 예시 화면을 자동 장애 분석의 성공 증거로 사용하지 않는다.
- 첫 단계는 Agent 없이 같은 Incident의 Metric·Log·배포·AWS/DB 상태를 읽고 근거를 구조화한다. 이 결과와 실제 주입 장애의 정답 근거를 확보한 뒤 AgentCore·Strands·Bedrock 보고서를 비교한다.
- Backend가 대표 `incidentId`와 에피소드의 계정·리전·Alarm ARN·서비스·환경·실제 `detectedAt`을 확정한다. 과거 ID는 기존 M3 상세 규칙대로 대표 ID로 연결한다. 모델이 만든 문자열, 브라우저가 보낸 scope, EventBridge 원본 JSON의 미검증 필드만으로 다른 대상에 접근하지 않는다.
- 초기 트리거는 **권한 있는 운영자의 명시적 분석 요청**을 후보로 한다. 중복 클릭·SQS 재전달·Alarm의 `OK` 또는 `INSUFFICIENT_DATA`만으로 별도 분석을 무한 생성하지 않는다. 자동 트리거 여부와 재분석 조건은 사용자 결정 대상이다.
- 하나의 `analysisRunId`는 대표 Incident ID, 분석 계약/프롬프트 버전, 시작 시각, 요청자/트리거와 연결한다. 상태는 `queued/running/completed/insufficient_evidence/failed/cancelled` 후보로 구분한다. 권한과 비용 상한을 확인한 뒤에만 실행한다.

## 읽기 MCP 도구 공통 입력·출력

도구는 임의 AWS 계정·리전·서비스·환경·GitHub 저장소를 LLM 입력대로 선택하지 않는다. Backend가 발급한 분석 실행 문맥의 대표 Incident와 서버 측 allowlist를 다시 확인하고, Agent가 요청한 시간 창·건수는 그 문맥 안에서만 **좁힐 수 있다**. `from`·`to`는 UTC RFC 3339이며 `from < to`다. 범위 밖 요청은 명시적으로 거절한다. 읽기 도구의 IAM·GitHub·DB 권한과 M5 변경 권한은 분리한다.

### 호출 공통 부분 — 제안 JSON

```json
{
  "analysisRunId": "run_example_1",
  "incidentId": "inc_1234567890abcdef1234567890abcdef",
  "window": {
    "from": "2026-10-28T00:45:00.000000Z",
    "to": "2026-10-28T01:15:00.000000Z"
  },
  "maxItems": 100
}
```

`analysisRunId`와 `incidentId`는 Backend가 서버 측 실행 문맥에 결합하며 도구는 둘의 대응을 확인한다. 요청에 account/region/service/environment를 받지 않는다. `maxItems`는 도구별 상한보다 클 수 없고 응답 바이트 수·호출 시간도 서버가 제한한다.

### 결과 공통 부분 — 제안 JSON

```json
{
  "toolCallId": "call_example_1",
  "analysisRunId": "run_example_1",
  "tool": "search_logs",
  "status": "ok",
  "queriedAt": "2026-10-28T01:15:03.000000Z",
  "scope": {
    "account": "123456789012",
    "region": "ap-northeast-2",
    "service": "slash-api",
    "environment": "dev"
  },
  "window": {
    "from": "2026-10-28T00:45:00.000000Z",
    "to": "2026-10-28T01:15:00.000000Z"
  },
  "items": [
    {
      "observedAt": "2026-10-28T01:01:04.000000Z",
      "summary": "허용된 로그의 비밀값 제거 후 발췌",
      "sourceRef": {
        "refId": "log-ref-1",
        "system": "cloudwatch_logs",
        "resource": "/aws/ecs/example",
        "id": "event-or-stable-reference"
      }
    }
  ],
  "truncated": false,
  "error": null
}
```

`observedAt`은 원본 Metric 구간/Log 이벤트/배포/AWS 상태의 **관측 시각**이고 `queriedAt`은 도구가 조회한 시각이다. 둘을 섞어 사건 순서를 만들지 않는다. 각 주장에 참조할 수 있도록 항목별 `sourceRef`를 남긴다. `refId`는 분석 실행 안에서 고정하며 보고서의 `sourceRefs`가 이를 가리킨다. 참조는 원본 시스템·허용된 리소스·식별자/조회 조건으로 재조회 가능해야 하며 서명 URL, 토큰, DB 접속 정보는 담지 않는다. 긴 원본 로그와 비밀값은 보고서나 Incident DSQL 세 테이블에 복사하지 않는다.

| `status` | 뜻 | 처리 |
| --- | --- | --- |
| `ok` | 조회가 완료됐고 허용된 근거가 있음 | 관측 사실의 후보로 사용 가능 |
| `no_data` | 조회는 성공했으나 범위 안에 결과가 없음 | 비어 있다는 관측만 기록. 정상·무장애라고 추론하지 않음 |
| `partial` | 시간·응답량 상한, 일부 소스 누락, 불완전 응답 | `truncated=true` 또는 경고와 함께 범위를 명시 |
| `query_failed` | 권한·네트워크·원본 서비스·시간 초과 등 조회 실패 | 오류 코드만 보고. 데이터 부재와 구분 |

도구가 실패해도 다른 독립 읽기 도구 결과는 남긴다. 전체가 실패하거나 근거가 부족하면 보고서 상태를 `insufficient_evidence` 또는 `failed`로 표시한다. 같은 실패를 무제한 재시도하지 않는다. 원본 시스템의 `no_data`, 도구의 `query_failed`, 응답 잘림 `partial`을 하나의 “정상” 상태로 합치지 않는다.

## 후보 도구별 최소 계약

| 도구 | 허용 입력 | 출력 근거와 금지 사항 |
| --- | --- | --- |
| `get_metrics` | Incident 문맥 안의 UTC 창, 허용된 M2 지표 키, 최대 60초 구간 수 | [실측 지표 API 초안](incident-metrics-api.md)의 5개 원본에서 도출한 3개 시리즈, 각 구간의 실제 원본 시각·품질·조회 조건. 결측을 0으로 만들지 않음 |
| `search_logs` | 서버가 정한 로그 그룹 allowlist, 제한된 검색 템플릿/문자열, UTC 창, 최대 건수·바이트 수 | 비밀값 제거 발췌, 실제 로그 시각, 그룹·스트림·이벤트 참조. Agent가 임의 CloudWatch Logs Insights 쿼리나 전체 로그 덤프를 만들 수 없음 |
| `get_deployments` | 허용된 GitHub 저장소·환경의 배포 조회, UTC 창, 최대 건수 | 배포 ID·버전/SHA·배포 시각·상태·sourceRef. “GitHub에 배포 기록 있음”과 “현재 AWS에서 그 버전 실행 중”을 구분. 실행 버전 연결이 없으면 미확인 |
| `get_resource_status` | 허용된 AWS 리소스 유형·ARN, UTC 창 또는 현재 스냅샷, 최대 건수 | 실제 상태·조회 시각·리소스 ARN·sourceRef. 현재 스냅샷을 과거 장애 시각의 상태로 소급하지 않음 |
| `get_db_status` | 허용된 운영 대상 DB 상태 점검 종류, UTC 창/스냅샷, 최대 건수 | 연결·지연 등 승인된 읽기 지표/상태와 시각. 임의 SQL·테이블 전체 조회·쓰기·운영 데이터 원문 반출 금지 |

Metric은 60초 **집계**, Log는 개별 **사건 기록**, Trace는 실제 계측된 요청 **흐름**이다. Trace 수집이 없으면 `trace_unavailable`을 미확인 항목으로 남긴다. 요청 ID·Trace ID를 CloudWatch Metric Dimension에 추가해 억지로 연결하지 않는다. Log의 요청 ID와 배포 버전은 실제로 기록된 경우에만 연결한다.

## 실행 한도와 실패 계약

첫 시나리오의 제안 상한은 **한 실행당 도구 호출 최대 8회, 모델 호출 최대 2회, 전체 120초, 로그 발췌 합계 256 KiB, 배포 기록 20건**이다. 개별 도구의 UTC 창·점 수·최대 건수·바이트 수와 AWS/모델 비용 예산도 서버에 강제해야 한다. 이 숫자들은 임시 후보이며 사용자 승인과 실제 비용 측정 전에는 운영 값이 아니다. 시간·비용 상한에 걸리면 `partial`/ `insufficient_evidence`로 종료하고 누락 도구·범위를 남긴다.

재시도는 읽기 도구의 일시적 실패에만 제한 횟수로 허용하고 동일 `analysisRunId` 안에 시도 이력을 남긴다. 인증/권한 거부·잘못된 대상·예산 초과는 재시도하지 않는다. 모델 호출 실패 시 도구가 수집한 근거를 보존하되 AI 결론을 만들어내지 않는다. Agent는 읽기 도구만 사용할 수 있고 M5 Action Tool을 직접 호출할 수 없다.

## RCA 보고서 계약

제안 필드: `reportId`, 대표 `incidentId`, `analysisRunId`, `generatedAt`, 분석 창, 분석 상태, 사용 모델·프롬프트·도구 계약 버전, 도구 호출 상태/비용 요약, 아래 항목 배열. 민감 원본 전문 대신 최소한의 발췌와 `sourceRef`를 저장한다. 표시 문구는 “AI 분석 결과”이며 사실을 넘어서는 결론은 “가설”로만 둔다.

| 구역 | 필수 내용 |
| --- | --- |
| `facts[]` 관측된 사실 | 하나의 검증 가능한 문장, 실제 관측 시각/창, 하나 이상의 `sourceRef`. 원본 수치·로그에서 직접 확인되는 내용만 |
| `hypotheses[]` 원인 가설 | 주장, 지지하는 `factId`, 반대 근거 ID, 불확실한 이유. 근거 없는 정밀 확률이나 “확정 원인” 표현 금지 |
| `counterEvidence[]` 반대 근거 | 어떤 가설을 약화하는지, 실제 원본 참조와 관측 시각. 결과가 없었다는 사실은 조회 완료·범위가 확인된 경우만 |
| `unknowns[]` 미확인 | 미계측 Trace, 배포 버전 연결 실패, 누락 구간, 도구 실패 등 원인을 확정할 수 없는 이유 |
| `nextChecks[]` 권장 다음 조회 | 읽기 확인 절차와 필요한 권한·시간 범위. M5 복구 실행 요청과 분리 |

보고서가 복구를 제안해도 **제안은 승인·실행 권한이 아니다**. M5에서는 별도 사용자 승인과 서버 재검증을 통과해야 한다. `OK` 전환, API 성공, 도구 조회 성공만으로 서비스 전체 복구나 RCA 정확성을 선언하지 않는다.

### 축약 예시 — 제안 형식, 실제 분석 아님

```json
{
  "reportId": "rca_example_1",
  "incidentId": "inc_1234567890abcdef1234567890abcdef",
  "analysisRunId": "run_example_1",
  "status": "insufficient_evidence",
  "generatedAt": "2026-10-28T01:16:00.000000Z",
  "facts": [
    {
      "factId": "f1",
      "text": "01:00 UTC 구간의 API 5xx 비율이 관측됐다.",
      "observedAt": "2026-10-28T01:00:00.000000Z",
      "sourceRefs": ["metric-ref-1"]
    },
    {
      "factId": "f2",
      "text": "GitHub 배포 기록에 00:55 UTC 완료가 표시됐다.",
      "observedAt": "2026-10-28T00:55:00.000000Z",
      "sourceRefs": ["deployment-ref-1"]
    }
  ],
  "hypotheses": [
    {
      "hypothesisId": "h1",
      "text": "최근 배포가 오류에 영향을 주었을 수 있다.",
      "supportingFactIds": ["f1", "f2"],
      "counterEvidenceIds": ["ce1"],
      "uncertainty": "실행 중 버전과 오류 요청의 연결이 확인되지 않았다."
    }
  ],
  "counterEvidence": [
    {
      "counterEvidenceId": "ce1",
      "text": "01:05 UTC AWS 상태 조회에는 이전 작업 버전이 표시됐다.",
      "observedAt": "2026-10-28T01:05:00.000000Z",
      "sourceRefs": ["resource-ref-1"],
      "weakensHypothesisIds": ["h1"]
    }
  ],
  "unknowns": [
    {
      "text": "요청 Trace가 계측되지 않아 실패 지점을 확인하지 못했다.",
      "reason": "trace_unavailable"
    }
  ],
  "nextChecks": ["실제 실행 중 버전과 오류 로그의 같은 시간대 연결을 확인한다."]
}
```

## 첫 주입 장애 평가 기준과 미결정 항목

1. 시험 전에 **주입한 장애의 원인·시작/종료·대상 서비스·환경·배포 버전·기대 로그/지표**를 별도 정답 기록으로 남긴다. Agent 보고서를 만든 뒤 정답을 바꾸지 않는다.
2. 도구별 조회 성공률·실제 원본 시각·범위·비용을 먼저 평가하고, 별도로 보고서의 사실 일치·가설의 지지/반대 근거·미확인 표기·허위 단정 여부를 비교한다. 도구 호출 성공을 RCA 정확도로 계산하지 않는다.
3. 지표 결측, 로그 없음, GitHub 배포는 있으나 실행 버전 연결 없음, 도구 시간 초과, 무관한 배포가 있는 **반례**에서도 보고서가 불확실성을 표시하는지 확인한다.
4. 같은 대표 Incident에 대한 중복 요청, 권한 없는 요청, 범위를 넘긴 도구 입력, 비용/시간 한도 초과를 검증한다. 소스 참조를 역추적할 수 있고 보고서에 비밀값·원본 로그 전문이 저장되지 않아야 한다.

**사용자 결정 전:** 첫 주입 장애와 정답 기준, 수동/자동 분석 트리거, 재분석·취소 권한, 실행 한도와 예산, 근거/보고서 보존 기간, 배포 버전을 실제 실행 버전과 연결할 방법, 보고서의 승인·표시 방식과 M5 제안으로 넘길 조건을 확정해야 한다.
