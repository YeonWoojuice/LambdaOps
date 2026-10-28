# LambdaOps frontend

승인된 `lambdaops-ui.pen`의 그래프 중심 운영 개요와 Incident 상세를 구현하는 React·TypeScript 프런트엔드입니다. Cloudscape는 화면 구조와 조작 요소에 사용하고, 시계열은 MIT 라이선스의 Recharts로 그립니다. 현재 화면은 **명시적으로 표시된 예시 데이터**로 동작합니다.

## 로컬 실행

Node.js 22.12 이상이 필요합니다. 저장소 루트에서:

```bash
cd frontend
npm ci
npm run dev
```

`npm run typecheck`, `npm test`, `npm run build`로 타입, 데이터 처리, 배포용 정적 파일 생성을 각각 확인할 수 있습니다. 빌드 결과는 `frontend/dist/`에 생성되며 현재 저장소에는 프런트 배포 설정이 없습니다.

## 현재 데이터 경계

- 예시 그래프는 CloudWatch 실측이 아닙니다. 5xx 비율, **평균** API 지연, **평균** DB 쿼리 지연의 화면 표현을 검토하기 위한 자료입니다.
- `src/data/`에는 기존 M3 `GET /incidents`와 `GET /incidents/{incidentId}` 계약에 맞춘 타입과 인증된 조회 클라이언트를 둡니다. 현재 앱은 인증 제공자가 연결되지 않아 예시 모드로 실행됩니다.
- 조회 클라이언트는 JWT를 헤더에만 넣고 HTTPS API 주소만 허용합니다.
- 실제 브라우저 연동에는 로그인·JWT 발급 방식, API Gateway의 CORS, 실제 M3 배포가 필요합니다. 그래프 실측에는 별도의 인증된 CloudWatch 지표 조회 API와 M2 지표 발행·배포 검증이 필요합니다.
- `OK`는 그 Alarm 에피소드의 종료입니다. 서비스 전체 복구나 Incident 해결을 뜻하지 않습니다. `INSUFFICIENT_DATA`는 복구가 아니며, 그래프의 결측은 실제 지표가 없는 구간에만 표시해야 합니다.

브라우저 코드에 AWS 자격 증명이나 고정 JWT를 넣지 않습니다. `VITE_` 환경 변수에도 비밀값을 넣지 마세요.
