# Phase 6-14 구현 지침: 합성 clarification React 흐름

## 목표

Phase 6-13 합성 ASGI prototype의 두 endpoint를 사용하는 별도 React 화면을 만든다. 팀원이 브라우저에서 질문 시작 → ambiguity option 선택 또는 취소 → submit 결과를 확인할 수 있어야 한다.

legacy `App`·`MainPage`·`ChatPopup` 흐름은 기준선으로 그대로 유지한다. 실제 Ollama, PostgreSQL, CNU 자료, auth와 연결하지 않는다.

## 진입점과 실행

- `/adaptive-prototype` 경로에서만 새 화면을 렌더한다. 다른 경로는 기존 `<App />`을 그대로 렌더한다.
- 새 router dependency를 추가하지 않는다. 현재 `window.location.pathname`을 작은 진입점 분기로 사용한다.
- Vite dev proxy에 `/api/adaptive`를 추가하고 기본 target은 `http://127.0.0.1:8123`으로 한다. 기존 `/api/v1` proxy는 변경하지 않는다.
- `package.json`에 backend prototype 단독 실행과 backend+Vite 동시 실행 script를 추가한다. backend는 Phase 6-13 module CLI를 그대로 사용하고 외부 bind/reload를 추가하지 않는다.
- 새 dependency를 설치하지 않는다.

## API client와 runtime 계약

- 새 코드는 `src/adaptive-prototype/` 아래 격리한다.
- start path: `/api/adaptive/v1/clarifications/start`
- submit path: `/api/adaptive/v1/clarifications/submit`
- database ID: `synth-clarification-prototype`
- TypeScript 타입 선언만 믿지 말고 모든 성공·오류 JSON을 runtime에서 엄격히 검사한다.
- 허용 start 상태는 `NOT_REQUIRED`, `PENDING`뿐이다. PENDING은 43~256자 URL-safe token과 2개 이상의 서로 다른 lowercase hex64 option ID, 유효 semantic ref가 필요하다.
- 허용 submit 상태는 `CANCELLED`, `EXECUTED`뿐이다. EXECUTED는 `SUCCEEDED` columns/rows 또는 고정 code/stage 실패 계약을 검사한다.
- HTTP 오류는 `{error:{code:string}}`만 수용한다. response text, exception message, request body, token을 UI·console·storage에 반사하지 않는다.
- fetch는 주입 가능하게 작성해 Node 내장 테스트에서 호출 횟수·body·응답 검증을 확인한다.
- `localStorage`, `sessionStorage`, cookie, auth header를 사용하지 않는다.

## React 상태와 안전 경계

- 상태를 명시적으로 분리한다: 입력 가능, start 진행, clarification pending, submit/cancel 진행, not-required, cancelled, executed success, executed failure, transport/contract error.
- 중복 클릭과 동시에 여러 요청을 차단한다.
- 각 네트워크 작업은 `AbortController`로 관리하고 unmount/reset 시 중단한다.
- session token은 pending 동안 메모리에만 보관한다. submit/cancel 시도 직전에 UI에서 재사용 불가능하게 만들고 성공·실패·abort 후 복원하지 않는다. 다시 시작하도록 안내한다.
- stale response가 새 요청 상태를 덮지 못하게 operation identity를 둔다.
- option label은 알려진 `Course.name`, `Instructor.name`에 한국어 표시명을 사용한다. 알 수 없는 semantic ref는 원문을 표시하지 않고 안전한 일반 label로 표시한다.
- 결과는 backend의 columns 순서로 generic table에 표시한다. cell은 제한된 JSON renderer로 표시하고 HTML 삽입을 쓰지 않는다.
- fixed error code는 제한된 한국어 메시지 표로 변환한다. 미지 code는 일반 오류로 표시하고 원문을 노출하지 않는다.
- 화면 상단에 `합성 데이터`, `로컬 전용`, `모델 정확도 아님`, `인증 없음`을 명확히 표시한다.
- SQL, QueryIR, token, physical mapping, 내부 trace를 표시하지 않는다.

## 테스트와 확인

새 dependency 없이 Node 24 내장 test runner와 type stripping을 사용한다.

- pure runtime parser/client/state helper를 `.ts`로 분리한다.
- `tests/frontend/`에 Phase 6-14 전용 테스트를 둔다.
- 정상 start PENDING/NOT_REQUIRED, submit success/failure/cancel을 검증한다.
- malformed/extra/duplicate option/잘못된 token/row-column mismatch/미지 상태를 거부한다.
- fetch URL·method·header·body가 정확한지, auth header와 storage 사용이 없는지 검증한다.
- error/exception/token 원문을 결과 message로 반사하지 않는지 검증한다.
- 관련 Node 테스트, 변경 frontend 파일 대상 ESLint, `npm run build`, Phase 6-13 backend prototype 테스트만 실행한다.
- 실제 browser 조작과 frontend/backend 실기동 smoke는 Phase 6-15로 남긴다.

## 작업 제한

- legacy `App.tsx`, `MainPage`, `ChatPopup`, `/api/v1` 동작을 변경하지 않는다. `main.tsx`의 경로 분기만 최소 변경한다.
- backend Phase 6-13/HTTP 계약을 변경하지 않는다.
- 실제 서비스, 외부 네트워크, Ollama, PostgreSQL, CNU 자료를 사용하지 않는다.
- 계획 문서, ADR, checklist, changelog, handoff는 수정하지 않는다.
- 전체 pytest, 전체 ESLint, compileall, `git diff --check`, commit, push는 실행하지 않는다.
- 긴 diff와 전체 로그를 출력하지 않는다. 변경 파일·핵심 계약·실패 원인·테스트 요약·남은 위험만 한국어로 간결히 보고한다.

