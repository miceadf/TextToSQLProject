# Phase 6-12: 합성 clarification HTTP 경계

Phase 6-11 `ClarificationSessionCoordinator.start/submit`을 legacy `backend/main.py`와 분리된 작은 FastAPI application factory로 노출한다. 목표는 두 POST endpoint, server-owned dependency lifecycle, 고정 오류→HTTP status 매핑, 응답 redaction을 합성으로 검증하는 것이다. 실제 legacy app mount, React UI, 인증, Ollama, PostgreSQL, CNU 자료는 제외한다.

## 먼저 확인

- `git status --short`, 최근 commit, `AGENTS.md`, handoff, 계획 Phase 6·ADR-039·다음 시작점, E3/L1/L3/L5를 따른다.
- `backend/main.py`는 route/path 관례만 확인하고 수정·import하지 않는다. import 시 legacy warmup/DB/model side effect가 생길 수 있으므로 새 모듈은 독립적이어야 한다.
- `clarification_session.py`와 Phase 6-08~11 관련 테스트만 읽는다.

## 구현 계약

1. 새 `backend/adaptive/api` package에 `clarification-http-v1` application factory를 둔다. `create_clarification_http_app(...) -> FastAPI`가 독립 app을 만들며 legacy global `app`, CORS, auth, startup warmup을 변경하지 않는다. endpoint는 정확히 `POST /api/adaptive/v1/clarifications/start`, `POST /api/adaptive/v1/clarifications/submit`이다.
2. factory는 동일한 Phase 6-11 coordinator 인스턴스, context loader, constraint-aware provider, read-only executor와 선택적 compiler/policy를 한 번 주입받아 두 endpoint closure가 재사용한다. 요청마다 coordinator를 새로 만들면 안 된다. 필수 callable은 app 생성 시 검증하고 잘못된 구성은 fixed `CLARIFICATION_HTTP_INVALID_CONFIGURATION`만 가진 구성 오류로 fail closed한다. 전역 mutable singleton은 만들지 않는다.
3. context loader는 `load(database_id)`로 server-owned runtime `Ontology`, Published `MappingProfile`, `DetectorRuleSet`을 반환한다. HTTP caller가 ontology/mapping/rules/pending/constraint/IR/model/executor/result를 주입할 필드는 없다. 반환 context는 dump-then-validate하고 mapping database ID가 요청과 일치해야 한다. loader 예외·부재·위조 context는 원문 없이 `CLARIFICATION_HTTP_CONTEXT_UNAVAILABLE`/HTTP 503으로 응답하고 session/provider/executor를 호출하지 않는다.
4. start request body는 `request_id`, `database_id`, `question`만 허용하고 strict extra-forbid·기존 길이 제한을 적용한다. malformed JSON, validation 오류, extra field는 body/token/question을 반영하지 않는 `CLARIFICATION_HTTP_INVALID_REQUEST`/HTTP 400으로 반환한다. FastAPI 기본 422 detail이 raw input을 돌려주지 않도록 이 app 전용 `RequestValidationError` handler를 둔다.
5. start endpoint는 server-loaded context와 request만 coordinator `start`에 전달한다. `NOT_REQUIRED` 응답은 version/status만, `PENDING` 응답은 version/status/session token과 option ID·semantic ref만 제공한다. request/database/ontology/mapping fingerprint, pending 전체, raw question/identifier/rule/trigger/physical mapping은 HTTP 응답에서 제외한다.
6. submit request body는 `session_token`, `selection`(opaque option ID 목록), `cancelled`만 허용한다. token은 body에만 있고 URL/header/log에 옮기지 않는다. endpoint는 `ClarificationChoice`를 구성해 같은 coordinator의 `submit`을 호출한다. caller context는 받지 않는다.
7. cancel 결과는 version/status `CANCELLED`만 반환한다. executed 결과는 version/status `EXECUTED`, 기존 harness `SUCCEEDED|FAILED`, columns/rows 또는 fixed error code/stage만 제공한다. SQL, QueryIR, constraint/generation, trace, request ID, fingerprint, 내부 error message는 항상 제외한다. 성공 rows는 JSON 값으로 재검증한다. 0행 성공을 실패로 바꾸지 않는다. compile/policy/executor/result-schema 실패는 HTTP 200의 redacted `EXECUTED`/`FAILED` domain result로 보존한다.
8. 오류 매핑은 단일 명시 표로 고정한다. 최소 규칙: malformed/input/invalid choice/ruleset=400, session not found=404, expired=410, consumed/conflict/context drift=409, constraint invalid/violation=422, provider error=502, context unavailable=503, adapter/session/internal·미지 오류=500. 응답은 항상 `{"error":{"code":"FIXED_CODE"}}` shape이며 message/detail/exception/input을 넣지 않는다. 기존 하위 fixed code를 가능한 그대로 사용하고 HTTP layer가 새 자유 문자열을 만들지 않는다.
9. endpoint 본문은 알려진 도메인 예외와 최종 unexpected exception을 모두 잡아 fixed response로 변환하여 exception 원문이 framework log/response로 탈출하지 않게 한다. app/모듈 자체는 print/log를 하지 않는다. access log 정책·proxy log·TLS·auth는 범위 밖임을 docstring에 명시한다.
10. response model은 strict/frozen/extra-forbid이며 상태별 불변식을 자체 validator로 강제한다. direct forged response에서 token/options 상태 불일치, success+error, failed+rows, cancelled+execution payload, SQL/IR/trace extra field를 거부한다.
11. 이 단계는 synthetic sub-application일 뿐 legacy app에 include/mount하지 않는다. 파일·network·Ollama·PostgreSQL·CNU 자료에 직접 접근하지 않는다. SQLite 실행은 테스트가 명시적으로 주입한 read-only executor를 통해서만 발생한다.

## 합성 테스트

- app factory의 정확한 두 POST route와 legacy `backend.main` 미import/미변경을 확인한다.
- no-match start 200 `NOT_REQUIRED`; 단일 match 200 `PENDING` 후 같은 app/coordinator의 submit 성공. entity/field, 0행 성공 포함.
- PENDING 응답은 token/options만, EXECUTED 성공은 columns/rows만 필요한 범위로 반환하며 SQL/IR/trace/fingerprint/raw ID/context는 없다.
- cancel 200 후 재사용 409, expired 410, unknown 404, 동시 제출은 하나만 200이고 다른 하나 409.
- malformed JSON, missing/extra/type/길이 오류가 400 fixed body이고 raw question/token/path/secret을 반영하지 않는다.
- loader 부재/예외/잘못된 DB·Draft mapping·위조 context는 503, coordinator/provider/executor 0회.
- ambiguity conflict/context drift, invalid/unknown/multi choice, constraint violation/provider error를 명시 표대로 매핑한다.
- compiler/policy/executor/result-schema 실패는 HTTP 200 domain failure로 기존 code/stage만 보존하고 SQL/message/trace/rows는 숨긴다.
- unexpected loader/coordinator/provider/response serialization 예외도 500 fixed body이며 stdout/log에 원문이 없다.
- factory dependency 검증, singleton coordinator 재사용, 공개 body/API에 forbidden injection field 부재를 검사한다.
- response model direct forgery와 row의 비 JSON 값을 fail closed한다.
- Phase 6-08/09/10/11 관련 회귀를 함께 실행한다.

OpenCode에서는 관련 테스트만 실행한다. 전체 pytest, compileall, `git diff --check`, 계획·ADR·handoff 갱신, commit은 Codex 독립 검토 뒤에만 수행한다. 실제 legacy mount와 React 사용자 흐름이 없으므로 상위 `모호한 질문 감지 및 사용자 확인` 체크는 미완료로 유지한다.
