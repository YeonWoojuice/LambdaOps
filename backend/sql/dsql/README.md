# M3 Incident DSQL 초기 스키마

승인한 [Incident 데이터 모델 설계](https://app.notion.com/p/3df91c6807b18154a44fca6a46ba3eca)를 실행 가능한 SQL로 옮긴다. `public` 스키마에 테이블 3개, 컬럼 32개, 인덱스 10개를 정의한다. Lambda에는 DSQL 선택 경로가 있지만 기본 설정과 Terraform은 DynamoDB다. 이 SQL만으로 실제 DSQL 자원이 생성되거나 연결되지는 않는다.

## 파일과 실행 순서

| 순서 | 파일 | 역할 |
| --- | --- | --- |
| 1 | [001_create_alarms.sql](001_create_alarms.sql) | 경보 식별 정보·최신 상태·처리 버전을 저장한다. |
| 2 | [002_create_alarm_events.sql](002_create_alarm_events.sql) | 전환 중복 키·이전 상태·원본 JSON 근거를 저장한다. |
| 3 | [003_create_incidents.sql](003_create_incidents.sql) | 장애 요약과 기존 ID의 연결 행을 저장한다. |
| 4 | [004_create_canonical_anchor_index.sql](004_create_canonical_anchor_index.sql) | 같은 근거를 사용하는 대표 Incident의 중복을 막는다. |
| 5 | [005_create_detected_index.sql](005_create_detected_index.sql) | 대표 Incident의 시간순 조회를 지원한다. |
| 6 | [006_create_service_environment_index.sql](006_create_service_environment_index.sql) | 서비스와 환경을 함께 지정하는 조회를 지원한다. |
| 7 | [007_create_redirect_index.sql](007_create_redirect_index.sql) | 대표가 변경될 때 기존 연결 행을 찾는다. |
| 8 | [008_verify_schema.sql](008_verify_schema.sql) | 인덱스 상태와 컬럼·제약조건 정의를 읽기 전용으로 확인한다. |

001~003의 PK 3개와 전체 UNIQUE 3개는 인덱스를 자동 생성한다. 004~007의 부분 인덱스 4개를 합쳐 총 10개다. FK 4개는 존재하는 Alarm·근거 이벤트·연결 대상을 참조하도록 제한한다. 추가 테이블이나 별도의 aliases 테이블은 만들지 않는다.

## 1. 테이블 생성

대상은 아직 이 테이블이 없는 초기 DB다. 연결 정보와 스키마 생성 권한을 먼저 준비한다. DSQL의 IAM 연결과 수집·조회 계정의 권한 분리는 후속 인프라 작업에서 구성한다. 연결 정보·토큰은 SQL 파일에 넣지 않는다.

DSQL에서는 한 트랜잭션에 DDL 하나만 실행할 수 있다. 001~007은 각각 DDL 하나로 구성하며, 생성 전체를 `BEGIN`이나 `psql --single-transaction`으로 묶지 않는다. DDL과 테스트 데이터 INSERT도 같은 트랜잭션에 넣지 않는다. [AWS DSQL DDL 제약](https://docs.aws.amazon.com/aurora-dsql/latest/userguide/working-with-ddl.html)

이 디렉터리에서, 준비한 `PGHOST`, `PGPORT`, `PGDATABASE`, `PGUSER`와 인증 설정을 사용한다. TLS는 DSQL 연결 요구사항에 맞춰 구성한다.

```sh
psql -X -v ON_ERROR_STOP=1 -v AUTOCOMMIT=on \
  -f 001_create_alarms.sql \
  -f 002_create_alarm_events.sql \
  -f 003_create_incidents.sql
```

`IF NOT EXISTS`는 사용하지 않는다. 같은 이름의 다른 정의를 정상 생성으로 오인하지 않기 위해서다. 중간 실패 시 앞선 파일은 이미 커밋됐을 수 있다. 무조건 처음부터 재실행하거나 테이블을 삭제하지 말고, 실제 정의와 실패 위치를 확인한 뒤 미완료 단계만 진행한다.

## 2. 인덱스 생성과 완료 확인

004~007을 **하나씩 실행하고, 각 작업의 성공을 확인한 뒤 다음 파일로 진행**한다.

```sh
psql -X -v ON_ERROR_STOP=1 -v AUTOCOMMIT=on \
  -f 004_create_canonical_anchor_index.sql
```

`CREATE INDEX ASYNC`의 결과로 받은 `job_id`는 작업 접수 번호다. 해당 값을 다음 명령의 `RETURNED_JOB_ID`에 넣어 완료 상태를 확인한다. 005~007도 같은 절차를 따른다.

```sh
psql -X -v ON_ERROR_STOP=1 -v job_id=RETURNED_JOB_ID <<'SQL'
SELECT sys.wait_for_job(:'job_id') AS completed;
SELECT job_id, status, details FROM sys.jobs WHERE job_id = :'job_id';
SQL
```

`completed`가 `t`여야 다음 단계로 진행한다. `f`, 조회 오류, 시간 초과는 성공으로 처리하지 않는다. **`false` 반환은 SQL 오류가 아니므로 `ON_ERROR_STOP=1`만으로 성공 여부가 판정되지는 않는다.** 이 절차는 수동 확인 절차이며 자동 배포 스크립트가 아니다.

실패한 인덱스는 INVALID 상태로 남을 수 있고, 실패한 UNIQUE 인덱스도 쓰기에 영향을 줄 수 있다. 원인을 확인하고 정리·재생성한 뒤 다시 검증한다. 작업 이력이 정리된 뒤에는 현재 카탈로그 상태로 판단한다. [AWS 비동기 인덱스](https://docs.aws.amazon.com/aurora-dsql/latest/userguide/working-with-create-index-async.html)

## 3. 설치 결과 확인

```sh
psql -X -v ON_ERROR_STOP=1 -f 008_verify_schema.sql
```

다음 항목을 모두 확인한다.

- 첫 결과는 `expected_index_count=10`, `found_index_count=10`, `all_expected_indexes_valid=t`여야 한다. 누락된 인덱스도 `f`로 판정한다.
- 인덱스 정의의 컬럼·순서·부분 조건이 001~007과 일치해야 한다. 첫 결과의 Boolean은 존재·소속·유일성·준비 상태를 확인하며, 정의 전체를 자동 비교하지는 않는다.
- 컬럼 수는 `alarms` 8개, `alarm_events` 11개, `incidents` 13개다. 자료형·길이·NULL·기본값을 확인한다.
- PK·UNIQUE·FK·CHECK의 실제 정의를 확인한다. 특히 같은 Alarm을 보장하는 복합 FK와 nullable 연결의 `MATCH SIMPLE`, OK 시각의 두 CHECK를 확인한다.

테이블 생성과 필수 인덱스 검증이 모두 끝난 뒤 수집·조회 서비스를 연결한다. 실제 DSQL에서 CHECK 식·카탈로그 쿼리·동시성·권한을 검증하기 전까지 AWS 환경 검증 완료로 표시하지 않는다.

## DB와 애플리케이션의 책임

| DB가 검사하는 조건 | 저장 어댑터에서 추가로 보장할 규칙 |
| --- | --- |
| 중복 이벤트 키, 같은 Alarm·전환 시각의 중복 | 같은 키로 들어온 이전 상태·시각의 모순 검사 |
| 근거 이벤트의 존재와 동일 Alarm | 실제 ALARM 근거인지, 같은 에피소드인지 검사 |
| 연결 대상의 존재·동일 Alarm·자기참조 금지 | 최종 대표 직접 연결, 순환 방지, 대표 선택 |
| 필수 값·허용 상태·시간 관계·문자열 길이 | 원본과 정규화 값의 일치, 탭·Unicode 공백 검사 |
| 대표 행의 동일 anchor 중복 금지 | 원본·요약·연결·Alarm 버전을 한 트랜잭션에 반영 |
| OK일 때만 종료 시각 존재, 마지막 전환 시각과 일치 | ID·생성 시각 고정, 변경 시 updated_at 갱신, 충돌 시 전체 재시도 |

`incident_state`는 M3에서 `NEW`만 허용한다. OK는 Alarm 에피소드 종료이며 서비스 전체 복구나 업무상 Resolved를 뜻하지 않는다. 여러 대표를 하나로 정리할 때에는 **제외할 대표를 연결 행으로 전환한 다음 유지할 대표의 anchor를 변경**한다.

## 로컬 제약조건 검증

별도의 비어 있는 PostgreSQL에 대해 [tests_sql](../../tests_sql/test_schema.py)을 명시적으로 실행한다. 기존 `backend/tests`와 분리되어 있다. CI에는 일회용 PostgreSQL을 준비한 뒤 이 모음과 `tests_dsql`을 각각 실행하는 단계를 추가했다. 일반 단위 테스트 명령 하나로 모든 SQL 검사가 실행되지는 않는다.

로컬 PostgreSQL에서는 001~003을 그대로 적용하고, 004~007은 **로컬 검증용 입력에서만** `INDEX ASYNC`를 `INDEX`로 바꿔 적용한다. 저장소의 DSQL 원본 파일은 바꾸지 않는다. 이 변환은 SQL 제약조건을 검사하기 위한 것으로, DSQL의 비동기 작업·OCC를 재현하지 않는다.

예를 들어 이 디렉터리에서 비어 있는 로컬 테스트 DB의 `PG*` 연결 정보를 설정한 후 다음 순서로 적용한다.

```sh
(
  set -eu
  set -o pipefail
  psql -X -v ON_ERROR_STOP=1 -v AUTOCOMMIT=on \
    -f 001_create_alarms.sql -f 002_create_alarm_events.sql -f 003_create_incidents.sql
  for ddl in 004_create_canonical_anchor_index.sql \
             005_create_detected_index.sql \
             006_create_service_environment_index.sql \
             007_create_redirect_index.sql; do
    sed 's/INDEX ASYNC/INDEX/' "$ddl" | psql -X -v ON_ERROR_STOP=1
  done
  psql -X -v ON_ERROR_STOP=1 -f 008_verify_schema.sql
)
```

중간에 오류가 있으면 테스트 실행 전에 원인을 해결한다. `backend/`에서 실행한다.

```sh
PGHOST=/absolute/path/to/local/socket \
PGPORT=55487 PGDATABASE=lambdaops_schema_test PGUSER=lambdaops_schema_test \
python3 -B -m unittest discover -s tests_sql -v
```

테스트는 각 사례의 데이터를 트랜잭션 안에 넣고 롤백한다. 실제 INSERT·UPDATE·DELETE의 성공과 SQLSTATE를 확인한다. 정상 ALARM·OK·INSUFFICIENT_DATA, NULL 조합, 잘못된 상태·ID·길이·JSON·시간, 중복, 다른 Alarm 참조, 연결 행과 부분 UNIQUE, FK 삭제 제한을 다룬다.

로컬 PostgreSQL에서 통과해도 DSQL에서 실행한 결과는 아니다. DSQL 연결·비동기 인덱스·OCC 재시도·실제 Lambda 경로는 [M2·M3 환경 검증](../../../docs/testing/m2-m3-verification.md)에 별도로 기록한다.
