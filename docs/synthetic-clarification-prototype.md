# 합성 Clarification 프로토타입 재현 절차

합성 전용 로컬 ASGI 프로토타입이다. 배포용이 아니며 인증이 없다.

## 안전 실행

```bash
.venv/Scripts/python.exe -m backend.adaptive.api.app_factory --port 8123
```

- host는 `127.0.0.1` 고정이며 외부 bind 옵션이 없다.
- port는 `8000-8999` 정수만 허용된다. reload와 access log는 꺼져 있다.
- 실제 DB URL, 모델 서버 설정, 환경 파일을 읽지 않는다.

## React 합성 화면

backend와 Vite를 loopback에서 함께 실행한다.

```powershell
npm run dev:adaptive-full
```

브라우저에서 `http://127.0.0.1:5173/adaptive-prototype`을 연다. 다른 경로는 기존 legacy 화면을 유지한다. 새 화면은 `/api/adaptive` proxy만 사용하며 legacy `/api/v1`이나 로그인 token을 사용하지 않는다.

Phase 6-15에서 실제 Chrome DOM·network browser smoke를 완료했다. 합성 내부 팀 논의용 준비 상태이며 외부 시연 완료나 최종 사용자 검증(L4)으로 표시하지 않는다.

팀 공유 실행 절차와 browser smoke 표는 [`docs/synthetic-clarification-team-handoff.md`](synthetic-clarification-team-handoff.md)를 사용한다.

## 고정 Database ID

```text
synth-clarification-prototype
```

이 ID 외의 database ID는 실패로 닫힌다.

## Start 예시

```json
{
  "request_id": "req-proto-001",
  "database_id": "synth-clarification-prototype",
  "question": "강의 목록 보여줘"
}
```

`POST /api/adaptive/v1/clarifications/start` 로 전송한다.
질문에는 `강의` 트리거가 포함되어야 `PENDING` 과 2개 option이 반환된다.

## Submit 예시

start 응답에서 받은 실제 값을 그대로 사용한다.

```json
{
  "session_token": "<start 응답의 session_token>",
  "selection": ["<선택한 option의 option_id>"],
  "cancelled": false
}
```

`POST /api/adaptive/v1/clarifications/submit` 로 전송한다.
`session_token` 을 손으로 만들거나 다른 세션 값을 재사용하지 않는다.

## 경계

- 합성 fixture(`DEFAULT_SEED` 기반 임시 SQLite)만 사용한다. 로컬 전용이다.
- 인증, rate limit, 영속 세션이 없다. 메모리 TTL/at-most-once 한계가 그대로 있다.
- 응답은 Phase 6-12 redaction 계약을 유지한다. SQL, 토큰 재사용 값, 임시 경로, 질문 원문은 응답에 포함되지 않는다.
- 결과는 합성 읽기 전용 재현용이며 모델 정확도나 실제 데이터 검증으로 해석하지 않는다.
