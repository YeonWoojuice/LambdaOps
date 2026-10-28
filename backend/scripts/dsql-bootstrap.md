# DSQL 초기화와 수집·조회 권한 분리

Terraform은 DSQL 클러스터와 Lambda IAM 역할을 만든다. DB 내부의 테이블·인덱스·사용자와 IAM 역할 연결은 `bootstrap_dsql.py`가 초기화한다. IAM 인증 토큰을 Terraform 상태나 파일에 저장하지 않기 위해 두 작업을 분리한다.

이 도구는 새 LambdaOps 전용 클러스터의 초기 설치용이다. 기존 데이터 이관이나 일반 스키마 마이그레이션 도구가 아니다. 승인된 `backend/sql/dsql/001`~`007` SQL은 그대로 사용한다.

## 실행 순서

1. Terraform에서 `dsql_schema_ready=false`로 클러스터·IAM 역할·Lambda를 먼저 생성한다. 준비 전에는 경보 수집과 큐 소비를 활성화하지 않는다.
2. Terraform의 `dsql_bootstrap` 출력으로 접속 대상과 역할의 정확한 값을 전달한다.
3. `preview`에서 생성할 SQL을 확인한 후 `schema`, `roles`, `verify` 순서로 실행한다.
4. 카탈로그·권한 검사가 통과해도 실제 Lambda의 IAM 연결을 별도로 확인한다. 그 후 준비 상태를 바꾸고 수집을 활성화한다.

저장소 루트에서 실행한다. 활성 Python 환경에는 `backend/requirements.txt` 의존성이 필요하다.

```sh
terraform -chdir=infra/environments/dev/m3 output -json dsql_bootstrap > /tmp/lambdaops-dsql-bootstrap.json
python backend/scripts/bootstrap_dsql.py preview --config /tmp/lambdaops-dsql-bootstrap.json
python backend/scripts/bootstrap_dsql.py schema --config /tmp/lambdaops-dsql-bootstrap.json
python backend/scripts/bootstrap_dsql.py roles --config /tmp/lambdaops-dsql-bootstrap.json
python backend/scripts/bootstrap_dsql.py verify --config /tmp/lambdaops-dsql-bootstrap.json
```

`--profile`로 로컬 AWS 프로필을 지정할 수 있다. 파일에는 엔드포인트와 역할 ARN만 있으며 자격 증명이나 토큰은 없다. 호출 계정이 출력의 계정과 다르거나 클러스터가 ACTIVE 상태가 아니면 연결 전에 중단한다. `preview`는 AWS에 접속하지 않는다.

관리자 실행 주체에는 `sts:GetCallerIdentity`, 대상 클러스터의 `dsql:GetCluster`와 `dsql:DbConnectAdmin` 권한이 필요하다. 이 권한을 수집·조회 Lambda에 부여하지 않는다. TLS 인증서와 호스트 이름을 검증하고 인증 토큰은 메모리에서만 사용한다.

## 명령별 동작

| 명령 | 수행 작업 | 실패 시 처리 |
| --- | --- | --- |
| preview | 대상과 실행할 SQL을 출력한다. | 설정이 잘못되면 실패한다. AWS 쓰기·읽기 모두 수행하지 않는다. |
| schema | public이 비어 있을 때 3개 테이블과 4개 ASYNC 인덱스를 개별 실행한다. 각 작업이 completed가 될 때까지 제한 시간 내 조회한다. | 기존·부분 스키마가 있으면 중단한다. 누락·실패·시간 초과를 완료로 간주하지 않는다. |
| roles | 별도의 LOGIN 역할과 최소 SQL 권한, 정확한 IAM 역할 매핑을 만든다. | 기존 역할은 검증만 한다. 부분 구성이나 다른 IAM 매핑은 자동 수정하지 않는다. |
| verify | 32개 컬럼, 35개 명명 제약, 10개 인덱스의 실제 정의·준비 상태와 유효 권한·매핑을 검사한다. | 개수만 맞아도 정의가 다르면 실패한다. 지원하지 않는 카탈로그 표현도 수동 확인 대상으로 실패 처리한다. |

DDL·DCL은 한 문장씩 autocommit으로 실행한다. 중간에 실패하면 앞선 단계가 이미 반영됐을 수 있다. 스크립트는 테이블·인덱스·역할을 삭제하지 않는다. 실패 후 처음부터 무조건 재실행하지 말고 `008_verify_schema.sql`, `sys.jobs`, `sys.iam_pg_role_mappings`와 실제 권한을 확인한다. 원인을 검토한 뒤 미완료 SQL만 수동 적용하고 `verify`를 다시 실행한다. DSQL 카탈로그·권한 캐시를 새로 읽기 위해 변경 후 검사는 새 연결에서 수행한다.

DSQL은 기본 키의 카탈로그 정의에 나머지 컬럼을 `INCLUDE`로 표시한다. 검증기는 기본 키와 전체 비키 컬럼의 자동 포함을 구분해 검사한다. 일부 컬럼만 포함하거나 키·정렬·고유성·조건이 달라진 인덱스는 승인된 설계와 다르게 판정한다. CHECK 식도 괄호의 의미를 유지한 채 비교한다.

## 권한 범위

| DB 역할 | alarms | alarm_events | incidents | 스키마 변경·삭제 |
| --- | --- | --- | --- | --- |
| lambdaops_ingest | SELECT·INSERT·UPDATE | SELECT·INSERT | SELECT·INSERT·UPDATE | 불가 |
| lambdaops_read | SELECT | SELECT | SELECT | 불가 |

각 DB 역할을 해당 Lambda IAM 역할 하나에만 `AWS IAM GRANT`로 연결한다. `dsql:DbConnect`의 클러스터 ARN 제한과 DB 내부 매핑을 함께 사용한다. 존재하지 않는 `dsql:DbUser` IAM 조건 키에 의존하지 않는다. 원본 이벤트 UPDATE, DELETE, TRUNCATE, 권한 재위임과 상속 역할은 허용하지 않는다.

DSQL의 public 스키마는 서비스가 관리한다. 일반 역할은 기본 USAGE 권한으로 접근하지만 이곳에 테이블을 생성할 수 없다. public 스키마 자체의 GRANT·REVOKE는 실행하지 않고, 두 역할에 USAGE가 있으며 CREATE와 권한 재위임이 없는지 먼저 검사한다. 이 세 테이블의 PUBLIC 권한은 별도로 제거한 뒤 두 역할에 필요한 테이블 권한만 부여한다. 중간에 역할만 생성된 경우에도 자동 복구하지 않고 실제 구성 확인 후 미완료 문장만 검토해 적용한다.

검사 성공은 DB 정의·권한 확인 결과다. 실제 Lambda의 역할 인증, 이벤트 처리, JWT와 HTTP 응답, 비용까지 검증됐다는 뜻은 아니다. 도구는 Terraform의 준비 변수를 자동으로 바꾸지 않는다.

공식 근거: [DB 역할과 IAM 매핑](https://docs.aws.amazon.com/aurora-dsql/latest/userguide/using-database-and-iam-roles.html), [비동기 인덱스](https://docs.aws.amazon.com/aurora-dsql/latest/userguide/working-with-create-index-async.html), [지원 SQL](https://docs.aws.amazon.com/aurora-dsql/latest/userguide/working-with-postgresql-compatibility-supported-sql-features.html), [DSQL 고려사항](https://docs.aws.amazon.com/aurora-dsql/latest/userguide/considerations.html).
