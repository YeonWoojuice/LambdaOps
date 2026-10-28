# ADR 0001: Incident DB로 Aurora DSQL 선택

- 결정일: **2026-10-28**
- 결정 상태: **선택 확정**
- 구현 상태: **DSQL 어댑터 구현·dev 기반 인프라 생성** — Terraform 기본 저장소는 DSQL. 실제 검사 결과는 [환경 검증 기록](../testing/m2-m3-verification.md) 참조
- 결정 근거: 사용자의 DSQL 선택이 [기존 Incident 데이터 모델 설계](https://app.notion.com/p/3df91c6807b18154a44fca6a46ba3eca)에 기록됨

## 배경과 결정

LambdaOps는 Alarm 에피소드당 목록·건수에 보이는 대표 Incident 한 건과 원본 전환 이력을 저장합니다. 이후 서비스·환경·상태·기간 필터, 검색, 정렬, 페이지 번호, 정확한 전체 건수 조회가 필요합니다. 사용자는 이러한 조회 요구를 SQL로 설계하기 위해 **Aurora DSQL을 Incident DB로 선택**했습니다. 목표 그림의 “Aurora DSQL 또는 유사 DB”는 대체 가능성을 표현하지만, 현재 선택을 자동으로 바꾸지는 않습니다.

이 결정은 **Incident 저장·조회**에만 적용됩니다. Terraform 상태 잠금용 DynamoDB는 다른 역할이므로 이 전환 대상이 아닙니다. EventBridge → SQS → Lambda 경로도 유지합니다. Spring Boot, Cognito, 사용자별 조회 권한은 이 결정만으로 확정되지 않습니다.

## 승인된 DSQL 모델과 생성 SQL

기존 [Incident 데이터 모델 설계](https://app.notion.com/p/3df91c6807b18154a44fca6a46ba3eca)는 하나의 DSQL 데이터베이스 안에 `alarms`(Alarm 식별·최신 상태·버전), `alarm_events`(중복을 제거한 전환과 원본 근거), `incidents`(대표 장애 기록과 기존 ID 연결용 기록) **3개 테이블**을 승인했습니다. 자료형·제약·키·인덱스와 검토 보완안도 승인됐으며, [초기 생성 SQL](../../backend/sql/dsql/README.md)에 32개 컬럼과 10개 인덱스를 정의했습니다. 로컬 PostgreSQL 제약조건 테스트 32개가 통과했고 실제 DSQL에도 승인 SQL을 적용해 정의·인덱스 완료 상태를 확인했습니다. 실제 DSQL에서 잘못된 입력·동시 쓰기를 처리하는 검증은 추가로 필요합니다.

처음 발급한 `incident_id`는 바꾸지 않습니다. 늦은 시작 근거가 도착하면 `detected_at`과 대표 이벤트 근거를 보정합니다. 같은 Alarm·에피소드로 확인된 기록은 최초 생성 시각·ID 순으로 대표를 선택하고 `incidents.redirect_to_incident_id`로 연결합니다. 연결 행은 목록·검색·건수에서 제외합니다. 상세는 대표를 200으로 반환하고 `requestedIncidentId`·`incidentId`·`redirected`로 연결을 표시합니다. [조회 계약](../contracts/incident-api.md). 다른 Alarm이나 서비스를 병합하지 않습니다.

원본·Incident·Alarm 버전은 한 트랜잭션으로 저장합니다. [DSQL 어댑터](../../backend/lambdaops_m3/dsql_store.py)는 SQLSTATE 40001과 알려진 UNIQUE 경합에서 전체 작업을 제한된 횟수만큼 재시도합니다. 로컬 PostgreSQL에서 원자성과 동시 수신을 검증했으며 실제 DSQL 검증은 남아 있습니다. DynamoDB 호환 코드는 상태전환시각으로 ID를 재계산하므로 DSQL의 고정 UUID 구현과 구분합니다.

## 보존할 계약

[Alarm 이벤트 계약](../contracts/alarm-event.md)의 에피소드당 대표 한 건, 계정·리전·ARN·상태·전환 시각 중복 키, OK의 에피소드 종료 의미, INSUFFICIENT_DATA 비종료, 역순 이벤트의 최신 상태 보호를 유지합니다. 원자적 저장·전체 재시도는 로컬 SQL로 검증하며 실제 AWS 결과는 별도로 기록합니다.

기존 [DynamoDB 어댑터](../../backend/lambdaops_m3/store.py)는 호환 경로로 유지합니다. [M3 Terraform](../../infra/modules/m3-core/main.tf)은 DSQL을 기본으로 선택하고 Lambda에 `INCIDENT_STORE=dsql`을 설정합니다. 기존 DynamoDB 테이블이 있는 배포에서는 `prevent_destroy`로 실수에 의한 삭제를 막으며 자동 이관은 하지 않습니다. DSQL의 `page`·`pageSize`와 기존 프런트의 `limit`·`nextToken`을 구분하고 공개 조회 연결 전에 프런트를 전환합니다. [현재 API 계약](../contracts/incident-api.md).

## 실제 서비스 연결을 위해 확인할 항목

1. 작성한 DDL의 실제 DSQL 적용·카탈로그·제약조건 검증과 보존 기간 결정.
2. 구현한 원자적 저장·연결·Alarm 버전·전체 재시도의 실제 DSQL 검증.
3. 확정한 검색·필터·번호 페이지·정확한 건수·이전 ID 상세 계약의 프런트 적용과 실제 JWT 검증. 현재 조회 주체는 단일 프로젝트 운영자이며 다중 사용자 격리는 별도 설계.
4. 수집 쓰기와 조회 읽기의 권한 분리, Lambda 연결, 스키마 초기화 및 배포 순서.
5. 대표 데이터 규모와 예상 호출 빈도로 저장·검색·건수 조회의 처리량과 비용 검증. 무료 운영이나 DynamoDB보다 싼 비용을 전제하지 않음.

순서는 **물리 스키마·조회 계약 설계 → Backend 전환 → Terraform·권한·연결 수정 → fixture·동시성 테스트 → AWS·비용 검증**입니다. 비용·기능상 다른 DB가 필요해지면 이 결정을 조용히 바꾸지 않고 별도 근거와 결정을 남깁니다. [검증 기준](../testing/m2-m3-verification.md).
