# LambdaOps Product Scope — 대화에서 확정된 제품 결정

> 문서 검토 기준: 2026-11-06. 이 파일은 ChatGPT 대화 「설계 반영 검토」(대화 ID: 6aeb0092-8334-83ee-a6fe-7277e9a273ae)에서 사용자가 직접 확정한 요구사항의 저장소 기준이다. [Notion Product Scope](https://app.notion.com/p/40d91c6807b1809297a9fc4c0bd16c5f)의 기존 관측 범위는 Review 상태로 유지한다. 여기의 목표 동작을 현재 구현이나 실환경 검증으로 읽지 않는다.

## 확정된 제품 요구사항 — 담당 팀 연결

사용자는 Slack의 기존 조직 정보를 활용하자는 제안에 “오케이 그렇게 하고”라고 답했고, 개인 대신 팀·부서를 추천하는 흐름의 설명 뒤 “ㅇㅋ 결정”이라고 확정했다.

- **PS-T01 · 추천 대상:** AI는 장애의 담당 개인이 아니라 **담당 팀·부서**를 추천한다. 추천 근거에는 원인 후보, 영향받은 서비스, 관련 기술 영역을 사용한다.
- **PS-T02 · 팀 정보:** Slack 사용자 그룹·채널 정보를 팀 후보의 우선 출처로 사용한다. 별도 인명·팀 등록을 기본 전제로 두지 않으며, 이 정보로 소유 관계를 알 수 없을 때 필요한 서비스 소유 정보만 보완한다. 채널 구성원이라는 사실만으로 담당 팀을 확정하지 않는다.
- **PS-T03 · 최종 배정:** 운영자가 AI 추천을 승인하거나 팀을 직접 선택한다. 주 담당 팀은 한 곳이며, 필요하면 협업 팀을 추가한다.
- **PS-T04 · 통지와 확인:** 최종 배정 뒤 팀에 알리고, ACK에는 실제 확인한 개인의 Slack 식별자와 확인 시각을 기록한다. 팀 추천과 개인의 확인 기록은 다른 정보다.
- **PS-T05 · 변경 이력:** 담당 팀을 재배정할 수 있고, 재배정 이력을 남긴다.
- **PS-T06 · 범위 경계:** 팀 내부에서 어느 구성원이 어떤 작업을 맡는지는 LambdaOps의 배정 범위 밖이다.

이 항목은 **제품 동작 결정**이다. Slack 연결 방식, 알림 채널·메시지 형식, ACK 기한, 재알림·에스컬레이션 규칙, 재배정 권한, API 경로와 DB 필드는 아직 이 결정으로 정해지지 않았다.

## 기존 문서와의 관계

- [Notion Product Scope](https://app.notion.com/p/40d91c6807b1809297a9fc4c0bd16c5f)는 평상시 기본 관측과 장애 시 심층 조사, ALB·ECS Fargate·Spring Boot·RDS PostgreSQL 관측 대상, AI의 근거 제시, 제외 범위를 기술한다. 이 페이지의 Stage는 **Review**이며, 본 대화가 기존 표의 모든 세부 사항을 새로 승인한 것은 아니다. 표의 RDS PostgreSQL은 **관측 대상 서비스의 DB**다.
- LambdaOps의 **Incident 저장소**는 별도 [Aurora DSQL 결정](https://github.com/YeonWoojuice/LambdaOps/blob/main/docs/decisions/0001-incident-db.md)을 따른다. [Alarm 이벤트](https://github.com/YeonWoojuice/LambdaOps/blob/main/docs/contracts/alarm-event.md)와 [Incident 조회 API](https://github.com/YeonWoojuice/LambdaOps/blob/main/docs/contracts/incident-api.md)의 에피소드·ID·조회 의미는 변경하지 않는다.
- [목표 아키텍처와 현재 구현](https://github.com/YeonWoojuice/LambdaOps/blob/main/docs/architecture/overview.md)은 승인 기반 복구를 장기 목표로 기술한다. Notion Scope가 제외한 자동 복구 실행을 이 문서가 허용하거나, [M4 분석 초안](https://github.com/YeonWoojuice/LambdaOps/blob/main/docs/contracts/rca-analysis.md)·[M5 복구 초안](https://github.com/YeonWoojuice/LambdaOps/blob/main/docs/contracts/recovery-approval.md)을 승인된 API/DB 계약으로 바꾸지 않는다.

## 상태와 남은 판단

| 구분 | 내용 |
| --- | --- |
| 확정·미구현 | PS-T01~T06은 제품 결정이다. 2026-11-06에 확인한 `main`에는 팀 추천·Slack 통지·ACK·재배정의 구현 근거가 없으며, 실환경 검증도 없다. |
| 미결정 | 정확한 Slack 그룹/서비스 소유 매핑, 통지·ACK·재알림·에스컬레이션 정책, 권한, 저장 구조, API와 UI 세부 동작. |
| 제안 | Notion의 MySQL, EKS/Kubernetes, Node.js/Python 확장은 “내 제안”이며 이번 대화의 확정 MVP 범위가 아니다. M4/M5의 세부 계약도 사용자 검토 전 초안이다. |
| 대화 근거 미확인 | 이전 답변의 요약에 등장한 SQL 입력값 미저장, GitHub 코드 읽기 연동, Incident별 자동 RCA, Slack 재알림·에스컬레이션은 이 대화에서 직접 승인된 발언이 확인되지 않았다. 별도 근거 확인 전에는 확정 요구사항으로 사용하지 않는다. |

현재 M3의 자동 Alarm→Incident 전체 경로와 실측 지표 연결도 [검증 기록](https://github.com/YeonWoojuice/LambdaOps/blob/main/docs/testing/m2-m3-verification.md)에 따라 미검증으로 구분한다. 이 문서는 아키텍처, 코드, API/DB 계약을 변경하지 않는다.
