# M2·M3 검증 기준과 증거 기록

이 문서는 **검증할 기준과 확인한 결과**를 구분해 기록합니다. 기존 [GitHub CI 기록](https://github.com/YeonWoojuice/LambdaOps/actions/runs/38544496325)과 아래 로컬 SQL 검증 이후, **2026-10-28에는 dev AWS에 DSQL·Lambda 기반 자원을 실제 생성하고 DB 권한·조회 Lambda 연결을 확인했습니다.** 과거 기록의 미배포 상태와 현재 결과를 구분하며 전체 M3 완료를 뜻하지 않습니다.

| 검증 단계 | 지금 있는 근거 | 아직 필요한 증거 |
| --- | --- | --- |
| M1 Slash 지표 | [M1 기록](https://app.notion.com/p/3ea91c6807b1819786b6e515e3a2a6f7)의 로컬 실측 | 실제 AWS 시험 환경과 동일한 계정·리전·서비스·환경의 지표 발행 확인 |
| M2 코드 | [Alarm 3종](../../infra/modules/m2-alarms/main.tf), CI의 Terraform 검사 | 선택한 임계값·최소 건수의 근거, 검토된 적용 계획, 실제 자원과 `ALARM→OK` 이력 |
| M3 로컬 코드 | 기존 저장·조회 검증과 초기화 도구 포함 단위 테스트 111개 통과 | 실제 DSQL 분산 충돌·성능과 이번 변경의 원격 CI 결과 |
| M3 AWS 기반 | 기반 자원 14개와 후속 JWT 보호 HTTP API 생성, 실제 DSQL·권한·로그인·빈 목록 200·401·403 확인 | 수집 Lambda의 실제 쓰기, EventBridge·SQS 전달·실패 큐, 실제 Incident 상세 200 |

## 2026-10-28 DSQL·권한·Lambda 실제 AWS 구성

대상은 계정 `642935793048`, 서울 리전 `ap-northeast-2`, `slash-api`/`dev`다. 기준 커밋 `6d4034c`에서 작업한 Terraform·초기화 도구·문서를 `3627cbb`로 커밋하고 GitHub main에 직접 반영했다. 이미 다른 프로젝트에서 사용하던 DSQL은 변경하지 않고 LambdaOps 전용 클러스터 `krugaq2rx2w52ku2bbp7e6piw4`를 생성했다.

| 검사 | 실제 결과 | 해석 |
| --- | --- | --- |
| Terraform 적용 | 14개 생성, 기존 자원 변경·삭제 0개 | DSQL 1, Lambda 2, IAM 역할 2·정책 2, SQS 3, 로그 그룹 2, SQS 소비 연결 1, DLQ 허용 정책 1 |
| DB 생성·설치 확인 | 테이블 3·컬럼 32·명명 제약 35·인덱스 10개 일치 | 001~007 승인 SQL 적용. ASYNC 작업 4개 모두 completed, PK·FK·CHECK·인덱스 카탈로그 비교 통과 |
| SQL 권한 | 역할 2·IAM 매핑 2 일치, 유효 권한 검사 통과 | 수집은 alarms/incidents SELECT·INSERT·UPDATE, alarm_events SELECT·INSERT. 조회는 세 테이블 SELECT만 허용. CREATE·DELETE·권한 재위임·상속 역할 없음 |
| IAM 정책 시뮬레이션 | 두 역할 × 대상/다른 클러스터 × 일반/관리자 접속 총 8개 기대 결과 일치 | 전용 클러스터 DbConnect만 allowed. DbConnectAdmin과 다른 클러스터는 implicitDeny. 시뮬레이션을 실제 접속 증거와 구분 |
| 조회 Lambda 직접 호출 | 6개 기대 응답 일치 | 기본·필터 목록 200 및 items=[]/totalItems=0, 미존재 상세 404, 기존 limit 거부 400, 인증 문맥 없음 401·scope 없음 403 |
| 배포 파일 | 두 Lambda Active/Successful, ZIP 해시 일치 | 아래 SHA-256의 Python 3.12 Linux x86_64 패키지로 실제 DB 조회 성공 |
| 재계획 | 자원 변경 0개, 최종 plan 종료 코드 0 | 준비 상태 반영 후 설정과 배포 상태 일치 |
| 최종 자동 테스트 | Python 3.12 Linux와 로컬 Python 3.13에서 각각 111개 통과 | 기존 91개 + 초기화 도구 20개. Terraform fmt·validate·diff 검사 통과. provider 6.x의 기존 호환 문법 deprecation 경고는 남음 |

조회 Lambda는 `lambdaops-dev-slash-api-de2b25b5-read`, 수집 Lambda는 `lambdaops-dev-slash-api-de2b25b5-ingest`다. 패키지 SHA-256은 `150734683949ac6fe946e88b507b6d8abe062d4a090788e7c6b7d509a2aea0f8`이다. [정리한 검증 결과](evidence/2026-10-28-dsql-provision.json)에는 비밀값 없는 대상·검사 결과만 보관한다.

실제 적용 중 두 차이를 확인했다. DSQL은 PK 제약과 인덱스에 비키 컬럼 INCLUDE를 자동 표시하므로 검사기가 전체 비키 컬럼과 정확히 일치하는 경우만 인정하도록 수정했다. 또한 DSQL이 관리하는 public 스키마의 CREATE 권한 변경은 거부되며, 사용자 역할은 이미 USAGE 허용·CREATE 금지 상태였다. 지원하지 않는 스키마 변경문을 제거하고 유효 권한을 검사하도록 수정했다. 부분 생성된 두 역할을 확인한 뒤 남은 테이블 권한·IAM 매핑만 적용했으며 테이블을 재생성하지 않았다. 이 동작은 [DSQL 고려사항](https://docs.aws.amazon.com/aurora-dsql/latest/userguide/considerations.html)과 [역할 연결 문서](https://docs.aws.amazon.com/aurora-dsql/latest/userguide/using-database-and-iam-roles.html)를 참고했다.

이 1차 적용 당시 설정은 `dsql_schema_ready=true`, `connect_m2_alarms=false`, `enable_http_api=false`였다. 후속 HTTP API 활성화 결과는 문서 끝에 기록한다. M2 상태 파일·대상 Alarm이 없어서 가짜 ARN을 넣지 않았으며 SQS 소비 연결은 Disabled다. 수집 Lambda의 환경변수·IAM/DB 권한은 구성했지만 실제 이벤트를 통한 쓰기는 아직 실행하지 않았다. 공개 HTTP API도 생성하지 않았다.

### GitHub 게시·CI 확인

2026-10-28 PR 없이 main에 `608ae8f`(스키마), `6d4034c`(어댑터·조회 계약), `3627cbb`(DSQL 인프라·권한·검증)를 게시했다. [CI 실행 40645586025](https://github.com/YeonWoojuice/LambdaOps/actions/runs/40645586025)는 `3627cbb1863aac80f7c792b4b5aea4e48cf7e0f0`에 대한 push 실행이며 세 job 모두 success다.

| GitHub 검사 | 결과 |
| --- | --- |
| Backend unit and SQL tests | 단위 111개·SQL 제약 32개·저장 및 조회 통합 23개 통과 |
| Frontend checks | 타입 검사·단위 테스트·배포 빌드 통과 |
| Terraform configuration | 형식 검사와 bootstrap·M2·M3 구성 검사 통과 |

이 실행은 AWS 배포를 수행하지 않았다. 단위·SQL 검사와 실제 환경 검증의 범위를 구분한다. 이후 Slash 배포 검토에서는 기존 개인 저장소의 미커밋 실험 구성을 읽기만 했으며, Slash 파일이나 AWS 자원은 변경하지 않았다.

**직접 호출에는 시험용 authorizer 문맥을 넣었다. 실제 JWT의 서명·발급자·audience 검증이나 API Gateway 통과 결과가 아니다.** 빈 DB의 연결·조회만 확인했으므로 데이터가 있는 상세의 200 응답, 이벤트 재생, 실패 큐, 분산 동시성, 성능·비용은 다음 검증 대상이다. M2와 M3는 In Progress를 유지한다.

## 2026-10-28 DSQL 생성 SQL의 로컬 검증

기준은 `185ba6a` 이후의 로컬 SQL 작업입니다. [생성·설치 확인 SQL](../../backend/sql/dsql/README.md)을 별도 PostgreSQL 18.4 임시 DB에 적용했습니다. 001~003은 그대로 실행하고, 004~007은 로컬 입력에서만 `INDEX ASYNC`를 `INDEX`로 바꿨습니다. DSQL 원본 SQL은 수정하지 않았습니다.

| 검사 | 결과 | 검증 범위 |
| --- | --- | --- |
| 테이블 생성 | 3개 테이블, 컬럼 8·11·13개 확인 | PostgreSQL 공통 SQL 문법·테이블 제약 |
| 인덱스 설치 확인 | 예상 10개, 실제 10개, 준비 상태 true | 이름·소속·유일성·valid/ready 확인 및 실제 정의 대조 |
| 누락 인덱스 감지 | 인덱스 하나를 트랜잭션 안에서 제거하자 실제 9개·준비 상태 false, 이후 롤백 | 일부 인덱스만 존재해도 성공으로 표시하지 않는지 확인 |
| [SQL 제약조건 테스트](../../backend/tests_sql/test_schema.py) | 32개 테스트 통과 | 정상 상태·NULL·길이·JSON·중복·FK·부분 UNIQUE·연결 전환 순서 |
| 기존 백엔드 테스트 | 46개 통과 | 현행 이벤트 처리·DynamoDB 테스트 대역·인증 조회 회귀 검사 |

제약조건 테스트는 각 사례를 롤백하며 종료 후 세 테이블의 데이터가 0건임을 확인했습니다. 임시 DB도 종료했습니다. 실제 DSQL의 DDL·비동기 인덱스·카탈로그 쿼리·OCC·IAM·Lambda 연결은 미검증입니다. PostgreSQL의 통과 결과를 AWS 환경 검증으로 사용하지 않습니다. 이후 어댑터 구현 작업에서 CI에 SQL 제약조건·통합 테스트의 별도 실행 단계를 추가했습니다.

## 2026-10-28 DSQL 저장·조회 어댑터의 로컬 검증

기준 커밋 `608ae8f` 이후 로컬 변경이다. 승인한 SQL은 변경하지 않고 DSQL 어댑터·연결 설정·번호 페이지 계약을 구현했다. 실제 자원·Terraform·프런트는 전환하지 않았다.

| 검사 | 결과 | 검증 범위 |
| --- | --- | --- |
| Python 단위 테스트 | **91개 통과** | 기존 46개 포함. 요청 검증·인증·IAM 연결 설정·충돌 재시도·고정 ID |
| 순서 변경 검증 | 단위 테스트 안에서 5,040개 순서·35,280개 중간 상태 확인 | ID·생성 시각 유지, 대표 선택·직접 연결. 메모리에서 순수 계산 로직 검증 |
| SQL 제약조건 | **32개 통과** | PostgreSQL 18.4에서 승인된 PK·FK·CHECK·UNIQUE 검사 |
| SQL 어댑터 통합 | **23개 통과** | 실제 INSERT/UPDATE/SELECT, 24개 수신 순서, 동시 중복·신규 전환, 커밋 응답 유실, 저장 한도 초과 롤백, 검색·필터·건수·읽기 스냅샷·HTTP/SQS 핸들러 |
| Lambda 패키지 | Python 3.12 Linux x86_64 ZIP import와 위 단위 테스트 91개 통과 | 바이너리 psycopg 3·TLS CA·두 핸들러·DSQL 모듈 로드, 실제 Lambda 호출 아님 |
| CI 구성 | YAML 읽기·변경 형식 검사 통과 | 일회용 PostgreSQL 서비스와 별도 제약·통합 테스트 단계 추가. 원격 Actions 실행 미확인 |

로컬 SQL 통합 테스트는 Python 3.13과 psycopg 3.3.6, PostgreSQL 18.4에서 실행했다. 단위 테스트는 생성한 ZIP을 사용해 Python 3.12 Linux에서도 검증했다. 패키지 SHA-256은 `150734683949ac6fe946e88b507b6d8abe062d4a090788e7c6b7d509a2aea0f8`이며 임시 산출물로 보관했다. 테스트 후 세 테이블이 비어 있음을 확인하고 임시 PostgreSQL을 종료했다.

현재 설정은 `INCIDENT_STORE=dsql`을 명시해야 새 경로를 선택한다. 실제 DSQL의 DDL·ASYNC·OCC·IAM/DB 역할·Lambda·JWT와 큰 데이터의 실행 계획·응답 시간·비용은 검증 전이다. 기본 DynamoDB 모드와 프런트의 `limit`·`nextToken`은 호환 경로로 남아 있으므로 실제 전환 시 함께 변경한다. M3는 In Progress로 유지한다.

## M2를 완료로 판단할 기준

실제 Slash 지표의 60초 발행, namespace `LambdaOps/Slash`, `Service`·`Environment`, 단위와 합계 값이 [M2 모듈 계약](../../infra/modules/m2-alarms/README.md)에 맞는지 확인합니다. 실측 트래픽으로 5xx 비율·API 평균 지연·DB 쿼리 평균 지연의 임계값과 최소 요청/쿼리 수를 정합니다. 검토된 Terraform 계획을 적용한 뒤 **Alarm 3개가 실제로 존재**하고, 제한된 시험에서 각각 `ALARM`과 후속 `OK`가 발생하는지 확인합니다. 지표 발행 확인이나 Terraform `validate`만으로는 이 기준을 충족하지 않습니다. 상세 적용 순서는 [dev 배포 안내](../../infra/environments/dev/README.md)를 따릅니다.

시험 전 이미지 보안 검토·예산·시험자 IP·종료 시각을 [기존 Slash 준비·종료 절차](https://github.com/YeonWoojuice/slash-personal/blob/main/infra/personal/README.md)에 따라 확인하고, 시험 후 결과 보존과 experiment 전체 삭제·잔여 자원 확인까지 검증합니다.

## M3를 완료로 판단할 기준

[기존 M3 종료 기준](https://app.notion.com/p/3ea91c6807b1818a988ddba8c22356e6)은 **`ALARM→Incident 1건 생성, 중복·OK·실패 처리, 인증된 Detail 조회 검증`**입니다. DSQL 기반 자원·권한·조회 Lambda 연결은 확인했으며, 실제 M2 이벤트·수집 Lambda 쓰기·기존 Incident 상세 200·프런트 연결과 전체 흐름 검증이 남아 있습니다. 실제 Cognito JWT의 빈 목록·401·403 검증은 아래 후속 결과를 따릅니다. Terraform 상태 잠금용 DynamoDB는 별개입니다.

실제 환경에서는 다음 결과를 남겨야 합니다.

1. CloudWatch Alarm → EventBridge → SQS → Lambda → Incident DB 각 구간에서 같은 전환을 추적하고, `ALARM` 한 에피소드가 목록·건수에서 대표 Incident 정확히 한 건으로 보이는지 확인.
2. 같은 이벤트의 재전달, `OK`, `INSUFFICIENT_DATA`, 역순 전달을 재생해 [이벤트 계약](../contracts/alarm-event.md)을 확인. `OK`를 서비스 전체 복구나 자동 `Resolved`로 기록하지 않음.
3. 실패한 SQS 레코드만 재시도되는지, EventBridge 전달 실패 큐와 소비 실패 큐에 남는 경우를 확인. 실패와 성공을 구분해 기록.
4. 실제 발급자·audience·scope의 토큰으로 `GET /incidents/{incidentId}`를 호출하고, 토큰 없음·읽기 권한 없음도 확인. 현재 전체 목록을 읽을 수 있는 사용자 범위가 의도와 맞는지 결정. [API 계약](../contracts/incident-api.md).
5. 늦은 시작 근거에도 최초 발급 Incident ID가 유지되는지, 같은 에피소드의 예전 ID를 연결한 뒤 목록·건수는 대표 한 건인지, 예전 ID로 상세를 조회할 수 있는지 검증.
6. 실제 Alarm 전이와 API 조회를 연결한 결과를 남기고, DSQL의 대표 데이터 저장·조회·검색·건수 비용을 측정. 배포 성공만으로 전체 흐름 완료로 표시하지 않음.

증거에는 **일시, AWS 계정·리전·환경, 사용한 코드 커밋, 시험 입력, 기대 결과, 실제 결과, 관련 로그·Alarm 이력·API 응답 링크**를 기록합니다. 토큰·비밀값·개인정보는 기록에 넣지 않습니다. M2와 M3의 상태는 기존 [마일스톤](https://app.notion.com/p/37684699df0043bd99cde242c7eeda19)과 작업 보드에서 갱신하고, 근거 없는 완료 표시를 하지 않습니다.

## 2026-10-28 실제 Cognito 조회 인증의 배포 준비

Slash 개인 foundation에 기존 web client와 분리된 LambdaOps operator client 및 resource server를 작성했다. 같은 개인 pool을 재사용하며, 읽기 scope는 `lambdaops/incidents.read`다. 기존 Slash callback `http://localhost:5173/callback`은 유지하고, 로컬 operator 로그인은 `http://localhost:8765/callback`의 code+PKCE를 사용한다. 이 설정은 생성 전에 준비한 코드다. 이후 foundation 적용과 현재 상태는 아래 후속 검증 결과를 따른다.

foundation 출력 `lambdaops_m3_auth_tfvars`는 `jwt_issuer`, operator client ID인 `jwt_audience`, `read_authorization_scope`를 제공한다. M3 Terraform은 같은 scope를 JWT 경로와 조회 Lambda에 전달한다. 일반 기본값 `incidents:read`를 바꾸지 않고 Cognito 배포 값으로 명시한다. 기존 web client의 `openid email profile`이나 client ID를 조회 권한으로 대신하지 않는다.

Slash 저장소의 `observability/personal/auth/auth.py`는 실제 배포 이후 code+PKCE 로그인과 읽기 프로브를 실행하도록 준비했다. 토큰은 저장소 밖의 새 600 권한 파일에 저장하고, 목록·상세 200, 토큰 없음 401, **같은 operator client에서 openid만 요청한 access token**의 403을 확인한다. 다른 client의 토큰은 audience 실패와 scope 실패를 혼동할 수 있어 403 시험에 사용하지 않는다. 목록만 확인한 결과나 미존재 상세 404를 인증된 Detail 완료 증거로 사용하지 않는다.

이 준비는 실제 JWT 검증 완료가 아니다. 로컬 fixture는 서명을 검증하지 않으며, 실제 Cognito·HTTP API가 생성된 뒤 그 발급자·audience·scope와 API Gateway 서명 검증을 통과한 기존 Incident의 상세 200을 남겨야 한다. 모든 초대 사용자가 operator client의 허용 scope를 요청할 수 있는 현재 개인 pool 범위이며, 별도 client를 사용자별 관리자 권한 경계로 간주하지 않는다. 다중 사용자·테넌트별 소유권 설계는 변경하지 않았다.

### 1단계 기록: Cognito·ECR foundation 생성 완료

2026-10-28 계정 `642935793048`·서울 리전에 Slash 개인 foundation을 실제 적용했다. ECR 저장소 6개·보관 정책 6개, Cognito pool·client 2개·resource server·domain으로 총 17개를 생성했으며 기존 자원 변경·삭제는 0개다. 적용 후 재계획도 변경 0개다. 이 작업은 LambdaOps의 DSQL·Lambda 재배포나 HTTP API 활성화를 포함하지 않는다.

| 확인 항목 | 실제 결과 |
| --- | --- |
| Cognito pool·domain | `slash-lab`, domain ACTIVE, 초대 사용자만 허용 |
| 앱 구분 | Slash web·LambdaOps operator의 callback·code flow·scope 일치, client secret 없음 |
| 로그인 기반 | 로그인 폼 HTTP 200, issuer discovery 200, 공개 서명 키 2개 조회 |
| 사용자·이미지 | 사용자 0명, ECR 저장소 6개 모두 이미지 0개 |
| 읽기 권한 연결값 | 실제 issuer·operator client ID·`lambdaops/incidents.read` 출력 확보 |

[Slash 생성·검증 근거](https://github.com/YeonWoojuice/slash-personal/blob/main/docs/observability/results/2026-10-28-foundation-provision.json)에 비밀값 없는 결과를 기록했다. state와 실제 tfvars는 개인 로컬 경로에 권한을 제한하고 Git에서 제외했다. 이 1단계 당시에는 사용자 로그인·토큰 발급·인증된 Detail 조회를 검증하지 않았다. 이어서 수행한 실제 로그인·HTTP API 결과는 다음 절을 따른다.


## 2026-10-28 실제 Cognito 로그인·HTTP 조회 검증

시험 사용자가 Cognito 화면에서 임시 비밀번호를 변경하고 code+PKCE 로그인을 완료했다. 비밀번호는 도구로 받지 않았으며 액세스 토큰은 Git 밖의 개인 파일로만 취급했다. 사용자 상태는 `CONFIRMED`지만 `email_verified=false`이므로 로그인 성공과 이메일 속성 검증은 구분한다. 이메일 소유권을 관리자가 임의로 확인 처리하지 않았다.

| 항목 | 실제 결과 | 의미 |
| --- | --- | --- |
| Terraform 적용 | 생성 8·변경 1·삭제 0 | HTTP API·JWT authorizer·통합·목록/상세 경로·stage·호출 권한 생성. read Lambda의 읽기 scope만 변경하고 배포 ZIP·DSQL 설정은 유지 |
| 후속 계획 | 변경 0 | 실제 상태와 명시한 `dev.tfvars` 일치 |
| 실제 로그인 + 읽기 scope | 목록 200, `items=[]`, `totalItems=0` | Cognito → API Gateway 서명·scope 검증 → 조회 Lambda → DSQL 연결 확인 |
| 토큰 없음·잘못된 토큰 | 목록·상세 각각 401 | 인증되지 않은 호출 차단 |
| 같은 client의 openid 전용 토큰 | 목록·상세 각각 403 | 유효한 로그인과 Incident 읽기 권한을 구분 |
| 읽기 scope + 없는 Incident ID | 404 | 상세 경로의 인증 및 미존재 처리 확인. 실제 기록의 상세 200을 대신하지 않음 |
| 수집 경로 | M2 미연결, SQS 소비 비활성 | 시험 데이터 생성·장애 실행·수집 Lambda 쓰기는 하지 않음 |

[개인정보를 제외한 환경 검증 결과](evidence/2026-10-28-cognito-http-verification.json)에 9개 HTTP 검사와 적용 범위를 기록했다. 무토큰 목록 검사는 기본 도구와 보조 검사에서 한 번씩 실행했다. 기존 사용자별 소유권 모델은 바꾸지 않았으며, 현재 초대 전용 개인 pool의 운영자 읽기 범위를 유지한다. 프런트 로그인·CORS 연결과 실제 Incident 상세 200, 이벤트·DLQ 전체 경로는 별도 검증이다. M3는 In Progress다.

## 2026-10-29 실제 브라우저 Incident 조회 시연 검증

로컬 `http://localhost:8765`의 프런트를 `live` 모드로 실행해 Cognito code+PKCE 로그인을 완료했다. API Gateway의 허용 origin을 이 주소로 한정해 Terraform에서 기존 HTTP API를 **변경 1·생성 0·삭제 0**으로 적용했다. 브라우저의 인증된 화면에서 Aurora DSQL에 저장된 대표 Incident **1건의 목록과 상세**를 확인했다. 목록에는 `1페이지 · 1건`, `ALARM`, `NEW`, `slash-api`가 표시됐고, 상세에는 같은 Incident ID와 Alarm 상태 이력·원본 JSON이 표시됐다. 무토큰 요청은 기존과 같이 401이며 CORS 사전 요청은 204를 반환했다.

조회한 `inc_5465b0e764084c4a837b84cd987ef7bf`는 관리자가 검증용 스크립트로 DSQL에 직접 넣은 **`DEMO` 시험 Incident**다. 원본 JSON에 `DEMO: direct administrator DSQL seed; not a CloudWatch or EventBridge delivery`라고 표시했다. 따라서 이 결과는 **Cognito → 인증된 브라우저 → API Gateway → 조회 Lambda → DSQL**의 목록·상세 읽기 경로를 입증한다. M2 Alarm 전이, EventBridge 전달, SQS 소비, 수집 Lambda의 자동 Incident 생성, CloudWatch 실측 그래프는 검증하지 않았다. M3 전체 Exit Criteria는 계속 In Progress다.


## 2026-10-30 다음 실환경 검증 착수 인계 — 적용 승인 전

PR #5의 인증된 DEMO 조회 작업은 사용자 승인으로 main에 병합됐다. 지표 화면의 선택 대표 Incident·기본 60분(-15/+45)·최대 6시간·완료된 60초 구간·수동 갱신·기존 읽기 scope 재사용 방향은 승인됐다. [지표 설계와 잔여 구현 결정](../contracts/incident-metrics-api.md)을 따른다. 이 절은 다음 시험의 준비 목록이며 AWS 현재 자원 조회·배포·자동 접수 성공 기록이 아니다. M4·M5 계약은 별도 승인 전이다.

| 순서 | 구현 에이전트가 준비할 내용 | 적용 또는 완료 전 필요한 근거 |
| --- | --- | --- |
| 1. 시험 대상 재확인 | 문서상 slash-api/dev·서울 계정의 실제 자원, Slash 저장소 위치·실행 이미지/버전, 기존 DB/Cognito/DSQL 유지 대상 확인 | 현재 상태와 과거 기록 구분. 이번 설계 검토에서는 Slash 코드 및 AWS 최신 상태 미확인 |
| 2. 배포 승인 자료 | 시험 방식·시험자 IP·시작/종료·예산·생성부터 정리까지 비용·보존 증거·삭제할 experiment 자원과 Terraform plan | 사용자 AWS 적용 승인. expires_at 태그를 자동 삭제 보장으로 취급하지 않음. 10/28 비용 가정은 최신 견적과 구분 |
| 3. M2 지표·Alarm | 동일 Service/Environment·60초 원본 5개와 실제 DB query timer 확인. 임계값 3개·최소 요청/쿼리 수 2개를 실측으로 결정 | 기본값 없는 M2 필수 입력 5개, Alarm 3종 각각 ALARM→OK 실제 전이. 지표 발행 중단을 정상으로 오해하지 않도록 별도 수집 상태 확인 |
| 4. M3 연결 | M2 remote-state alarm_arns와 DSQL 스키마/권한 확인. ingestion_enabled·dsql_schema_ready·connect_m2_alarms의 활성화 계획 제시 | 적용 전 별도 승인. 임의 ARN으로 대체하지 않고 실제 출력 사용 |
| 5. 끝까지 추적 | 같은 전환의 EventBridge→SQS→수집 Lambda→DSQL→인증된 웹 목록·상세를 추적 | 대표 1건·중복/역순·OK/INSUFFICIENT_DATA·실패 재시도/DLQ·최초 ID 보존. DEMO 읽기와 구분 |
| 6. 종료 확인 | 증거 보존 후 승인된 experiment 정리·잔여 자원 확인 | 유지할 DSQL·Cognito·상태 저장소를 임의 삭제하지 않음. 정리 완료와 잔존 비용 보고 |

지표 API 구현 인계 시에는 실제 M2 설정 전달 방식, 요청률·동시성·CloudWatch 재시도/비용 한도, 보존 기간 밖 데이터, HTTP/시간 경계 세부안을 먼저 구체화한다. 실제 실측 연결 전에는 예시 그래프를 실측으로 표시하지 않는다.


### M3 연결 전 중단 조건과 활성화 확인

다음 항목은 **실행할 준비 절차**다. 이 문서 작성에서는 AWS 조회·연결·시험 메시지 전송을 하지 않았다.

- DSQL 모드의 실제 활성화 식은 `ingestion_enabled && dsql_schema_ready && connect_m2_alarms`다. 일반 저장 모드는 `ingestion_enabled && storage_ready && connect_m2_alarms`이고, `storage_ready = !dsql_enabled || dsql_schema_ready`다. 스키마 준비 플래그는 검증을 대신하지 않는다. 실제 테이블·인덱스 완료, IAM/DB 역할과 수집 쓰기 권한의 확인 근거가 없으면 중단한다.
- 실제 M2 remote-state `alarm_arns`의 세 ARN, provider의 계정·리전, `service/environment`가 일치해야 한다. placeholder·다른 계정·다른 Alarm·임의 작성 ARN이면 중단한다. [M3 root](../../infra/environments/dev/m3/main.tf)와 [모듈 gate·전달 규칙](../../infra/modules/m3-core/main.tf)을 대조한다.
- 검토한 plan의 입력 파일·패키지 해시·대상과 적용 직전 값이 다르면 재검토한다. 승인 전 `apply`, 수집 활성화, DB 쓰기, 장애 주입·메시지 전송, 큐 정책 변경·redrive·삭제를 실행하지 않는다. 기존 Cognito·DSQL·상태 저장소 교체/삭제가 포함되면 별도 변경으로 보고한다.
- [다음 배포 준비](../../infra/environments/dev/README.md)의 예산·시험 IP·종료 시각·정리 대상이 미확정이거나 종료 담당이 없으면 실환경 시험에 착수하지 않는다.

### 실제 Alarm 전이와 합성 재생의 구분

최초 자동 접수 증거는 제한된 Slash 시험에서 발생한 **실제 CloudWatch Alarm 상태 전환**으로 얻는다. EventBridge 이벤트 ID·Alarm ARN·상태 전환 시각부터 SQS message ID·수집 로그·대표 Incident까지 연결한다. AWS 예약 source를 사용한 임의 `PutEvents`나 직접 SQS 전송을 실제 CloudWatch 전달로 표시하지 않는다.

기존 [fixture](../../backend/tests/fixtures/alarm.json)는 계정 `123456789012`, 고정 Alarm ARN·2026-10-08 시각을 쓴다. 실환경에 원본을 그대로 보내지 않는다. 승인된 시험 사본을 준비할 때 `account`, `region`, `resources`, `detail.alarmName`, `time`, 현재/이전 상태의 시각을 같은 대상·상태 사슬로 맞추고 합성 시험임을 기록한다. 이벤트 ID만 바꾸는 것으로 새 전환을 만들 수는 없다. 중복 키는 계정·리전·Alarm ARN·상태·전환 시각이며, 중복 시험에서는 이 값과 본문을 유지한다.

합성 재생은 실제 에피소드와 섞이지 않는 시험 시각·대상 범위를 검토한 뒤 별도 승인한다. 같은 Alarm의 실제 후속 전환과 합성 시각이 충돌하면 중단한다. 실제 이벤트를 그대로 재전달하는 중복 시험과, 상태/시각을 만든 역순 시험을 증거에서 구분한다. 원본·시험 사본·전송 순서를 남기며 승인되지 않은 실제 기록 정리나 seed 재사용으로 건수를 맞추지 않는다.

### M3 시험별 입력·기대 결과·증거

| 시험 | 준비할 입력과 기대 결과 | 보존할 근거 |
| --- | --- | --- |
| 실제 자동 접수 | 실제 ALARM 전이 하나에서 대표 Incident 한 건 생성 | Alarm ARN·실제 전환 시각·EventBridge 이벤트 ID·SQS message ID·수집 Lambda 로그·대표 ID·필터된 목록/건수 |
| 동일 전환 중복 | 동일 중복 키·동일 본문 재전달. 대표 ID와 건수 유지 | 원본/재전달 message ID, `outcome=duplicate`, 전후 API 요약 |
| 역순·늦은 시작 근거 | 승인된 순서표로 OK/ALARM 등 재생. 최신 상태 역행 없음, 최초 ID 유지 | 입력 사슬과 전송 순서, 각 단계의 상세/요약, 기존 ID의 대표 연결 |
| INSUFFICIENT_DATA·OK·다음 ALARM | 데이터 부족은 에피소드 종료가 아님. OK로 해당 에피소드 종료, 다음 ALARM은 새 대표 | 실제/합성 구분, 상태 이력·`alarmOkAt`·대표 건수. M3 업무 상태 NEW 유지 |
| 일시적 처리 실패 | 별도 승인된 시험 실패 후 재시도에서 동일 전환 한 건으로 수렴 | 실패 message ID·시도·재시도 로그·저장 결과. 실패 원인과 성공 시각 |
| 지속 소비 실패 | 잘못된 이벤트 등 독성 메시지는 성공으로 지우지 않고 소비 DLQ로 이동 | 실패 코드·재시도 횟수·해당 DLQ 식별자/시각·회복/수동 조치 계획 |
| EventBridge 전달 실패 | 별도 승인된 실패 주입. 소비 실패와 구분 | EventBridge 전달 실패/재시도와 전달 DLQ 근거. 정책 변경·원복 범위 |
| 인증된 웹 조회 | 자동 접수 대표 ID의 목록·상세, 무토큰 401·읽기 scope 없음 403 | issuer/audience/scope의 비밀값 없는 확인, 응답 상태·같은 ID·이력·CORS. JWT 원문 제외 |

[수집 핸들러](../../backend/lambdaops_m3/ingest_handler.py)는 레코드별 실패를 `batchItemFailures`에 넣고, [SQS 연결](../../infra/modules/m3-core/main.tf)은 `ReportBatchItemFailures`를 설정한다. 기본 `ingest_batch_size=1`이므로 기본 환경의 단일 레코드 시험만으로 **한 배치의 성공/실패 혼합 처리**를 검증했다고 쓰지 않는다. 혼합 배치 시험은 별도 설정 검토와 실제 배치 크기 증거가 필요하다. 대응하는 로컬 기준은 [HandlerTests](../../backend/tests/test_incidents.py)다.

코드상 소비 재시도는 `maxReceiveCount=5`, visibility timeout은 수집 timeout의 6배다. EventBridge 재시도는 최대 24회·이벤트 수명 3,600초이고 두 DLQ의 보존은 14일이다. 시험 전 실제 적용값을 다시 확인하고, 결과 대기 시간과 중단 시각을 그 값에 맞춰 정한다. 단순 큐 건수나 소비 DLQ가 비었다는 사실만으로 처리 성공을 판정하지 않는다.

### 조회 명령 템플릿 — 실행하지 않은 예시

아래는 승인된 대상 확인 후 사용할 조회 예시다. 출력의 대상이 승인 자료와 다르면 진행하지 않는다. `terraform output`은 실제 backend 접근이 필요하며 로컬 설정·상태 전체를 출력하거나 Git에 보관하지 않는다.

```bash
terraform -chdir=infra/environments/dev/m3 output -json ingestion_active
terraform -chdir=infra/environments/dev/m3 output -raw ingest_lambda_name
terraform -chdir=infra/environments/dev/m3 output -raw ingest_queue_url
terraform -chdir=infra/environments/dev/m3 output -raw eventbridge_dlq_url
terraform -chdir=infra/environments/dev/m3 output -raw consumer_dlq_url
```

```bash
aws lambda list-event-source-mappings \
  --function-name '<verified-ingest-lambda-name>' \
  --region '<verified-region>'
aws sqs get-queue-attributes \
  --queue-url '<verified-queue-url>' \
  --attribute-names ApproximateNumberOfMessages ApproximateNumberOfMessagesNotVisible \
  --region '<verified-region>'
```

`receive-message`는 메시지 가시성을 바꾸므로 단순 조회와 다르다. DLQ 본문 확인·redrive·delete·purge는 별도 시험 절차로 승인받고 실행 이력을 남긴다. 토큰은 브라우저의 정상 로그인으로 취급하고 채팅·명령 예시·증거 파일에 붙여 넣지 않는다. 로그·원본 응답을 보존할 때도 비밀값과 개인정보를 먼저 제거한다.


## 2026-10-30 current readiness and staged execution plan

PR #6 is merged. [Current read-only evidence](evidence/2026-10-30-aws-readiness.json) and [image/cost review](../../infra/environments/dev/README.md) supersede earlier unknown-current-state notes for the scoped resources only. Current DSQL/Lambda/queues exist, collection is Disabled, M2 Alarm and Slash runtime matches are absent, and last-hour request/query metric points are empty. This is preparation evidence, not M2/M3 completion.

| Stage | Concrete work and required artifact | Gate before proceeding |
| --- | --- | --- |
| A. Image readiness | Implementation agent resolves API Java findings, verifies source/build/runtime/config digests, and connects DB highfix2 provenance/function evidence; exact ECR digests and scan timestamps recorded | Reviewed candidates required. Existing Basic scans alone are insufficient. Image changes/publication require their own implementation scope; DB test remains disabled until separately approved |
| B. Baseline plan | Verify actual target/profile, current inventory, tester IP, runtime composition and secrets references without exposing values. Produce a saved Slash baseline Terraform plan and cleanup plan | Request user AWS approval with plan hash, image digests, start/test-stop/cleanup deadlines and proposed incremental budget. Current proposal: lifecycle <=5h, $5 incremental experiment allowance; no approval implied |
| C. Approved baseline only | After AWS approval, create the reviewed baseline, confirm authentication/private fault boundary and continuous five-source 60s metrics, record normal samples | No fault/load test is approved by this document. Record actual sample populations and telemetry continuity before choosing thresholds |
| D. M2 plan | Fill three thresholds and two positive minimum counts using measured samples; produce M2 saved plan, expected initial three Alarms only | User approval for actual apply; unexpected changes/destroy or unmeasured defaults stop progression. No executable M2 plan has been produced yet because current baseline and five numeric inputs are unavailable |
| E. Fault-test plan | Select bounded private 5xx/API-delay/DB-delay methods with exact targets, rate/concurrency/count, duration and abort thresholds; verify DB tool readiness | Separate user approval for load/fault execution. DB cancel-one or a single failed request does not prove 2-of-3 evaluation periods. If DB tool is not ready, report M2 partial rather than claiming three-Alarm completion |
| F. M3 plan and test | Wire actual M2 remote-state outputs, recheck DSQL schema/grants/package, review enablement plan; trace actual CloudWatch event through queue/Lambda/DSQL/JWT web and run approved replay/failure cases | Separate apply and test approval as applicable. Keep previous evidence matrix, batch-size limitation and synthetic-vs-real distinction. Each broken hop stops advancement |
| G. Cleanup and report | Preserve sanitized results, compare reviewed cleanup plan, remove only approved experiment scope, recheck remaining resources and forecast/actual available charges | Do not delete retained LambdaOps/Cognito/ECR/state. End time is cleanup verified, not merely load stopped or ECS desired count zero |

Ready now: scoped AWS inventory, candidate-image gaps, current unit-price estimate and staged test/evidence plan. Still needed before an AWS execution approval request: acceptable API image, DB-tool provenance/test decision, actual baseline and cleanup saved plans, tester IP, named stop/cleanup owner and agreed time/budget. M2 numeric thresholds follow approved baseline measurement; they are not fabricated from historical two-point samples. M4/M5 contracts remain drafts and are not part of these tests.


## 2026-10-30 image gate revalidation — latest remote source

The earlier Stage A blocker used a stale local Slash branch. Current remote main `3c11a99` already contains the API/DB remediation. [API reverification](evidence/2026-10-30-api-image-reverification.json) records 294 matching build inputs, fresh OS/Java findings 0 and synthetic local smoke 28/28; the full PG16/PG18 569-tests-each runs were prior evidence, not rerun here. [DB verification](evidence/2026-10-30-db-image-verification.json) records the highfix2 manifest/config/7 layers, tracked 7-file hashes, fresh OS findings 0 and new PG16/PG18 local tests 34/34. Only owned temporary test resources were cleaned; original Slash working-copy changes were preserved.

Stage A's missing source/function evidence is now resolved for these exact candidate digests within the stated provenance limits. The old Boot 3/API and old DB highfix1 findings remain historical facts about different candidates and must not be assigned to the reviewed Boot 4/highfix2 images. Keep the [current image review](../../infra/environments/dev/README.md) and evidence timestamps together. No image was posted by this task; the API digest's newer ECR publication was observed read-only during verification.

Next stage: produce and review the baseline and cleanup plans with these exact digests, current runtime/PostgreSQL/config selection, tester IP, owner and time/budget. Plan preparation may read current AWS configuration, but image promotion, apply/deploy, load/fault tests and M4/M5 contract acceptance still require their explicit approvals. Local DB cancellation tests do not prove AWS RDS behavior, HTTP 500, CloudWatch transitions or automatic Incident ingestion. No M2 or M3 milestone is marked complete by these image tests.


## 2026-10-30 baseline saved plan — no deployment

PR #8 was merged at `b9ab1a9`. The reviewed images are now pinned by full digest in a real Slash baseline saved plan: **98 create / 0 update / 0 destroy**, with init/validate and contract checks passing. [Sanitized plan evidence](evidence/2026-10-30-baseline-plan-review.json) and the [current approval/cleanup packet](../../infra/environments/dev/README.md#baseline-saved-plan-review--2026-10-30-1543-kst) update Stage B. Raw plan, tester IPv4 and inputs remain private. No AWS resource was deployed, image published or load/fault test executed.

The protected plan uses a copy of empty experiment local state; its current-state destroy plan has zero actions and is **not** the future cleanup plan. Before execution, replan against the canonical state after confirming the approved schedule/budget, current IP and RDS version. PostgreSQL 18.3 was observed as the regional default, while the saved plan leaves engine version unknown until apply. The 98-address creation allowlist bounds future cleanup review; foundation and LambdaOps resources must remain outside it.

M2 numeric thresholds still require actual observations. This does not complete any M2 Alarm transition, M3 automatic ingestion, AI RCA or recovery. The proposed next AWS stage is deployment plus normal authentication/telemetry checks only; deployment and any load/fault testing require separate approvals.
