# LambdaOps frontend

승인된 `lambdaops-ui.pen`의 그래프 중심 운영 개요와 Incident 상세를 구현하는 React·TypeScript 프런트엔드입니다. Cloudscape는 화면 구조와 조작 요소에 사용하고, 시계열은 MIT 라이선스의 Recharts로 그립니다. 현재 화면은 **명시적으로 표시된 예시 데이터**로 동작합니다.

## 로컬 실행

Node.js 22.12 이상이 필요합니다. 저장소 루트에서:

```bash
cd frontend
npm ci
npm run dev
```

개발 서버는 기존 LambdaOps 운영자 Cognito client의 callback에 맞춰 `http://localhost:8765`에서 실행합니다. 기본값은 예시 모드입니다. 실제 조회를 준비할 때는 [공개 설정 예시](.env.example)의 값을 `.env.local`에 채우고 `VITE_DATA_MODE=live`로 선택합니다. `live` 모드는 Cognito code+PKCE 로그인 후 받은 access token으로 DSQL Incident API를 호출하며, 필수 설정이 없으면 예시 데이터로 조용히 전환하지 않습니다. `auth.py`도 8765 포트를 사용하므로 브라우저 시연 중에는 함께 실행하지 않습니다.

`npm run typecheck`, `npm test`, `npm run build`로 타입, 데이터 처리, 배포용 정적 파일 생성을 각각 확인할 수 있습니다. 빌드 결과는 `frontend/dist/`에 생성되며 현재 저장소에는 프런트 배포 설정이 없습니다. 브라우저의 실제 Cognito 로그인·CORS·Incident 목록/상세 조회는 아직 실환경 검증 전입니다.

## 현재 데이터 경계

- 예시 그래프는 CloudWatch 실측이 아닙니다. 5xx 비율, **평균** API 지연, **평균** DB 쿼리 지연의 화면 표현을 검토하기 위한 자료입니다.
- `src/data/`에는 DSQL M3의 `page`·`pageSize` 목록·전체 건수와 `GET /incidents/{incidentId}`의 대표 ID 연결 계약에 맞춘 타입과 인증된 조회 클라이언트를 둡니다. 별도 설정이 없으면 앱은 예시 모드로 실행됩니다.
- 조회 클라이언트는 JWT를 헤더에만 넣고 HTTPS API 주소만 허용합니다.
- 실제 브라우저 연동에는 검토한 M3 CORS 설정의 AWS 적용과 Cognito 로그인·JWT·실제 Incident 목록/상세 조회 검증이 필요합니다. 그래프 실측에는 별도의 인증된 CloudWatch 지표 조회 API와 M2 지표 발행·배포 검증이 필요합니다.
- `OK`는 그 Alarm 에피소드의 종료입니다. 서비스 전체 복구나 Incident 해결을 뜻하지 않습니다. `INSUFFICIENT_DATA`는 복구가 아니며, 그래프의 결측은 실제 지표가 없는 구간에만 표시해야 합니다.

브라우저 코드에 AWS 자격 증명이나 고정 JWT를 넣지 않습니다. `VITE_` 환경 변수에도 비밀값을 넣지 마세요.
