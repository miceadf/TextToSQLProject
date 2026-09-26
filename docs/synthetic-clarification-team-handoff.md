# 합성 Prototype 팀 공유 Handoff (Phase 6-15)

Phase 6-14 `/adaptive-prototype` 화면과 Phase 6-13 loopback ASGI backend를 함께 실행하는 팀 공유용 내부 prototype 정리다. 합성 fixture 전용이며 legacy 교체, 실제 서비스 연결, 외부 시연 승인이 아니다.

## 사전 조건

- 저장소 루트에서 실행한다.
- Python 가상환경 `.venv`와 npm 의존성이 준비되어 있어야 한다.
- `8123`과 `5173` loopback port가 비어 있어야 한다.
- 실제 CNU 데이터, `.env`, PostgreSQL, Ollama, private recording이 필요하지 않으며 사용하지 않는다.

## 실행

단일 명령:

```powershell
npm run dev:adaptive-full
```

접속 URL:

```text
http://127.0.0.1:5173/adaptive-prototype
```

위 명령은 합성 prototype backend(port `8123`, host `127.0.0.1` 고정)와 Vite frontend를 함께 실행한다. 개별 실행이 필요할 때만 아래 명령을 사용한다.

```powershell
npm run dev:adaptive-prototype
npm run dev:frontend
```

## 종료 방법

- 실행 터미널에서 `Ctrl+C`를 누른다.
- `dev:adaptive-full`은 `concurrently -k`로 두 프로세스를 함께 종료한다.

## 브라우저 smoke 시나리오

2026-09-27 설치된 Chrome headless의 실제 DOM·network 경로로 관찰했다. 이는 기계적 browser smoke이며 사람의 시각·사용성 검토(L4)는 아니다.

| # | 시나리오 | 기대 관찰 | 상태 |
| - | -------- | --------- | ---- |
| 1 | `강의 목록 보여줘` 시작 | `강의명 기준`·`교수자명 기준` 두 option 표시 | `PASS_BROWSER_SMOKE` |
| 2 | `강의명 기준` 선택 후 제출 | 합성 강의명 결과 6행 표시 | `PASS_BROWSER_SMOKE` |
| 3 | 새 흐름에서 `교수자명 기준` 선택 후 제출 | 합성 교수자명 결과 6행 표시 | `PASS_BROWSER_SMOKE` |
| 4 | 명시적 취소 | `취소되었습니다` 종료 안내 표시 | `PASS_BROWSER_SMOKE` |
| 5 | trigger 없는 질문 | 추가 확인 불필요 안내 표시 | `PASS_BROWSER_SMOKE` |
| 6 | backend 연결 실패 | 안전한 일반 오류 표시; token, SQL, 경로, endpoint, 원시 응답 노출 없음 | `PASS_BROWSER_SMOKE` |
| 7 | terminal 상태 후 같은 session 재제출 시도 | option·제출·취소 control 제거로 재제출 불가 | `PASS_BROWSER_SMOKE` |
| 8 | `/` 경로 접속 | 기존 `Course Registration` 화면 유지 | `PASS_BROWSER_SMOKE` |

browser smoke는 기본 Vite proxy와, adaptive target만 사용하지 않는 loopback port로 바꾼 별도 Vite process를 사용해 연결 실패도 재현했다. backend token at-most-once의 직접 `409` 계약은 기존 자동 테스트(`tests/adaptive/test_synthetic_clarification_prototype.py`)가 담당하고, browser에서는 terminal UI가 같은 session의 제출 control을 제거하는 것을 확인했다.

## 공유 계약과 안전 경계

- 합성 전용: 고정 database ID `synth-clarification-prototype`와 `DEFAULT_SEED` 기반 임시 SQLite만 사용한다.
- loopback-only: backend host는 `127.0.0.1` 고정이며 외부 bind 옵션이 없고, port는 `8000-8999`만 허용된다.
- 무인증: 로그인 token, 계정, 권한, rate limit이 없다.
- 비영속 session: 메모리 session이며 TTL/at-most-once 한계가 있다.
- 응답 redaction: SQL, session token 재사용 값, 임시 경로, 질문 원문, 원시 응답을 UI와 응답에 노출하지 않는다.
- frontend는 `/api/adaptive` proxy만 사용하며 legacy `/api/v1`과 인증·storage를 사용하지 않는다.
- 결과는 합성 읽기 전용 재현용이며 모델 정확도나 실제 데이터 검증이 아니다.

## 알려진 한계

- 실제 화면의 시각 품질·용어 이해·사용자 행동은 L4 사람 검토 전이다.
- 장시간 서버 운용, 영속 session, 인증/rate limit, 실제 Ollama/PostgreSQL/CNU 자료, 성능 측정은 범위 밖이다.
- `E3/L5`와 `L1/L3` 사람 관문 결정 없이 실제 데이터 사용·공유, 배포·외부 시연, 최종 성능 주장을 하지 않는다.
