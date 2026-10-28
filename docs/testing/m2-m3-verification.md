# M2·M3 검증 기준과 증거 기록

이 문서는 **검증할 기준과 확인한 결과**를 구분해 기록합니다. 기존 [GitHub CI 기록](https://github.com/YeonWoojuice/LambdaOps/actions/runs/38544496325)에서 Backend 자동 테스트와 Terraform 형식·구성 검사 통과를 확인했습니다. 이번 SQL 작업에서 실행한 로컬 검사는 아래 절에 별도로 기록합니다. 기존 [README](../../README.md)는 2026-10-14 당시 M2·M3 미배포를 기록하며, **이번 작업에서 AWS 자원 상태를 직접 조회하거나 변경하지 않았습니다.**

| 검증 단계 | 지금 있는 근거 | 아직 필요한 증거 |
| --- | --- | --- |
| M1 Slash 지표 | [M1 기록](https://app.notion.com/p/3ea91c6807b1819786b6e515e3a2a6f7)의 로컬 실측 | 실제 AWS 시험 환경과 동일한 계정·리전·서비스·환경의 지표 발행 확인 |
| M2 코드 | [Alarm 3종](../../infra/modules/m2-alarms/main.tf), CI의 Terraform 검사 | 선택한 임계값·최소 건수의 근거, 검토된 적용 계획, 실제 자원과 `ALARM→OK` 이력 |
| M3 로컬 코드 | [Fixture와 테스트](../../backend/tests/), CI의 Backend 검사 | DSQL 전환 후 같은 규칙의 테스트와 실제 저장소·동시성 검증 |
| M3 AWS 전체 흐름 | [수집·조회 Terraform](../../infra/modules/m3-core/main.tf)과 [Backend](../../backend/lambdaops_m3/)의 정의 | 실제 전달·저장·실패 큐·JWT 조회·DSQL 연결을 보여 주는 결과 |

## 2026-10-28 DSQL 생성 SQL의 로컬 검증

기준은 `185ba6a` 이후의 로컬 SQL 작업입니다. [생성·설치 확인 SQL](../../backend/sql/dsql/README.md)을 별도 PostgreSQL 18.4 임시 DB에 적용했습니다. 001~003은 그대로 실행하고, 004~007은 로컬 입력에서만 `INDEX ASYNC`를 `INDEX`로 바꿨습니다. DSQL 원본 SQL은 수정하지 않았습니다.

| 검사 | 결과 | 검증 범위 |
| --- | --- | --- |
| 테이블 생성 | 3개 테이블, 컬럼 8·11·13개 확인 | PostgreSQL 공통 SQL 문법·테이블 제약 |
| 인덱스 설치 확인 | 예상 10개, 실제 10개, 준비 상태 true | 이름·소속·유일성·valid/ready 확인 및 실제 정의 대조 |
| 누락 인덱스 감지 | 인덱스 하나를 트랜잭션 안에서 제거하자 실제 9개·준비 상태 false, 이후 롤백 | 일부 인덱스만 존재해도 성공으로 표시하지 않는지 확인 |
| [SQL 제약조건 테스트](../../backend/tests_sql/test_schema.py) | 32개 테스트 통과 | 정상 상태·NULL·길이·JSON·중복·FK·부분 UNIQUE·연결 전환 순서 |
| 기존 백엔드 테스트 | 46개 통과 | 현행 이벤트 처리·DynamoDB 테스트 대역·인증 조회 회귀 검사 |

제약조건 테스트는 각 사례를 롤백하며 종료 후 세 테이블의 데이터가 0건임을 확인했습니다. 임시 DB도 종료했습니다. 실제 DSQL의 DDL·비동기 인덱스·카탈로그 쿼리·OCC·IAM·Lambda 연결은 미검증입니다. PostgreSQL의 통과 결과를 AWS 환경 검증으로 사용하지 않습니다. SQL 테스트는 별도 실행 명령을 사용하며 기존 CI의 `backend/tests` 실행에 자동 포함되지 않습니다.

## M2를 완료로 판단할 기준

실제 Slash 지표의 60초 발행, namespace `LambdaOps/Slash`, `Service`·`Environment`, 단위와 합계 값이 [M2 모듈 계약](../../infra/modules/m2-alarms/README.md)에 맞는지 확인합니다. 실측 트래픽으로 5xx 비율·API 평균 지연·DB 쿼리 평균 지연의 임계값과 최소 요청/쿼리 수를 정합니다. 검토된 Terraform 계획을 적용한 뒤 **Alarm 3개가 실제로 존재**하고, 제한된 시험에서 각각 `ALARM`과 후속 `OK`가 발생하는지 확인합니다. 지표 발행 확인이나 Terraform `validate`만으로는 이 기준을 충족하지 않습니다. 상세 적용 순서는 [dev 배포 안내](../../infra/environments/dev/README.md)를 따릅니다.

## M3를 완료로 판단할 기준

[기존 M3 종료 기준](https://app.notion.com/p/3ea91c6807b1818a988ddba8c22356e6)은 **`ALARM→Incident 1건 생성, 중복·OK·실패 처리, 인증된 Detail 조회 검증`**입니다. Aurora DSQL의 3개 논리 테이블과 ID 보존·연결 규칙은 승인됐지만 현재 M3 코드·Terraform은 DynamoDB이므로, 물리 스키마·어댑터·인프라 전환과 로컬 회귀 검증을 먼저 끝냅니다. Terraform 상태 잠금용 DynamoDB는 그대로 별개입니다.

실제 환경에서는 다음 결과를 남겨야 합니다.

1. CloudWatch Alarm → EventBridge → SQS → Lambda → Incident DB 각 구간에서 같은 전환을 추적하고, `ALARM` 한 에피소드가 목록·건수에서 대표 Incident 정확히 한 건으로 보이는지 확인.
2. 같은 이벤트의 재전달, `OK`, `INSUFFICIENT_DATA`, 역순 전달을 재생해 [이벤트 계약](../contracts/alarm-event.md)을 확인. `OK`를 서비스 전체 복구나 자동 `Resolved`로 기록하지 않음.
3. 실패한 SQS 레코드만 재시도되는지, EventBridge 전달 실패 큐와 소비 실패 큐에 남는 경우를 확인. 실패와 성공을 구분해 기록.
4. 실제 발급자·audience·scope의 토큰으로 `GET /incidents/{incidentId}`를 호출하고, 토큰 없음·읽기 권한 없음도 확인. 현재 전체 목록을 읽을 수 있는 사용자 범위가 의도와 맞는지 결정. [API 계약](../contracts/incident-api.md).
5. 늦은 시작 근거에도 최초 발급 Incident ID가 유지되는지, 같은 에피소드의 예전 ID를 연결한 뒤 목록·건수는 대표 한 건인지, 예전 ID로 상세를 조회할 수 있는지 검증.
6. 실제 Alarm 전이와 API 조회를 연결한 결과를 남기고, DSQL의 대표 데이터 저장·조회·검색·건수 비용을 측정. 배포 성공만으로 전체 흐름 완료로 표시하지 않음.

증거에는 **일시, AWS 계정·리전·환경, 사용한 코드 커밋, 시험 입력, 기대 결과, 실제 결과, 관련 로그·Alarm 이력·API 응답 링크**를 기록합니다. 토큰·비밀값·개인정보는 기록에 넣지 않습니다. M2와 M3의 상태는 기존 [마일스톤](https://app.notion.com/p/37684699df0043bd99cde242c7eeda19)과 작업 보드에서 갱신하고, 근거 없는 완료 표시를 하지 않습니다.
