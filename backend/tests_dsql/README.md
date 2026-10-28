# DSQL 저장·조회 어댑터 로컬 통합 검증

실제 SQL로 어댑터와 HTTP·SQS 핸들러를 검증한다. PostgreSQL에서 테스트하며 실제 DSQL의 IAM 인증, 비동기 인덱스, 분산 충돌 처리나 AWS 배포가 검증된 것은 아니다.

## 실행 조건

별도의 비어 있는 로컬 PostgreSQL에 **`lambdaops_adapter_test`** 데이터베이스를 준비한다. 테스트는 매 사례 전후에 이 데이터베이스의 `alarms`, `alarm_events`, `incidents`를 `TRUNCATE`한다. 일반 개발 DB나 보존할 데이터가 있는 DB를 사용하지 않는다.

먼저 [DSQL SQL 실행 안내](../sql/dsql/README.md)의 로컬 절차로 001~007을 적용한다. 004~007의 `INDEX ASYNC`는 테스트 입력에서만 `INDEX`로 바꾼다. 저장소 원본은 유지한다. 테스트 실행 환경에는 `backend/requirements.txt`의 의존성, 특히 psycopg 3이 필요하다.

`backend/`에서 다음 값을 실제 로컬 시험 환경에 맞춰 실행한다.

```sh
LAMBDAOPS_DSQL_TEST=1 \
PGHOST=/absolute/path/to/local/socket PGPORT=55488 \
PGDATABASE=lambdaops_adapter_test PGUSER=lambdaops_schema_test \
python -B -m unittest discover -s tests_dsql -v
```

- `LAMBDAOPS_DSQL_TEST=1`이 없으면 이 테스트 모음은 건너뛴다.
- DB 이름이 다르거나 로컬 주소·Unix 소켓이 아니면 실행을 거부한다.
- 환경의 연결 경로가 바뀌지 않도록 `PGHOSTADDR`, `PGSERVICE`, `PGSERVICEFILE`을 비워야 한다.
- 기존 `tests` 및 `tests_sql`과 별도 모음이다. CI에 일회용 PostgreSQL과 이 모음의 실행 단계를 추가했다. 원격 실행 결과는 실제 실행 후 확인한다.

## 검증 범위

| 범위 | 확인하는 결과 |
| --- | --- |
| 상태·이력 | ALARM 생성, OK 종료 기록, INSUFFICIENT_DATA 비종료, 지연·역순 이벤트, 실제 이력만 반환 |
| 중복·모순 | 최초 원본과 분류 보존, 모순된 이전 상태와 같은 시각의 다른 상태 거부 |
| 고정 ID·연결 | 시작 근거 보완 시 ID 유지, 생성 시각·ID 기준 대표 선택, 이전 ID 직접 연결과 중복 건수 제외 |
| 원자성 | 이벤트 INSERT 후 예외에도 일부 행이 남지 않음, 커밋 응답 유실 후 재수신 안전성 |
| 동시 저장 | 중복 이벤트 및 서로 다른 전환의 동시 저장 결과에 중복·누락 없음 |
| 목록 조회 | 동일한 필터·권한·대표 조건의 목록과 전체 건수, 페이지 번호, 정렬, 검색 특수문자 |
| 읽기 일관성 | 조회 도중 다른 트랜잭션이 커밋해도 목록·건수와 상세·이력이 같은 시점 유지 |
| 핸들러 | 검증된 JWT context·읽기 scope, 잘못된 요청 거부, SQS 실패 레코드만 재시도 응답 |

PostgreSQL의 동시성 검증은 실제 SQL 트랜잭션에서 발생하는 충돌을 다룬다. DSQL의 진단 코드·서비스 제한·IAM 역할·실제 Lambda 호출은 AWS 환경에서 별도로 확인해야 한다.
