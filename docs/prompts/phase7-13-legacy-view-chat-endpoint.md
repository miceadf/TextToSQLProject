# Phase 7-13 구현 지침: legacy view 대상 QueryIR 챗봇 질의 endpoint

## 배경

7-12에서 legacy 챗봇의 view `v_course_info`를 PostgreSQL profile v2(`legacy-view`)로 등록했다. 7-13은 그 DB에 대해 **자연어 질문 하나를 QueryIR 경로로 답하는 endpoint**를 prototype 서버에 추가한다. 7-14에서 `ChatPopup`이 이 endpoint를 부르고, 7-15에서 legacy 챗봇과 같은 질문으로 비교한다.

legacy 챗봇은 모델이 쓴 SQL을 view에 그대로 실행하고 결과 행을 강좌 카드로 보여준다. 새 경로도 결과는 강좌 카드로 보여주지만, 다음 세 가지를 지킨다.

- 모델은 QueryIR만 만든다.
- 실행은 기존 검증 → compiler → AST policy → read-only executor 경로만 거친다.
- SQL과 물리 이름은 응답에 넣지 않는다.

## 범위

### 1. `backend/adaptive/api/chat_query.py`

- `POST /api/adaptive/v1/chat/query`, 요청 `{"database_id", "query"}`
  - strict JSON이어야 하고, 추가 key·중복 key는 거부한다.
  - body는 기존 review 경계 한도 이하, 질문은 1~500자다.
- 대상: 서버가 검증한 PostgreSQL context만 받는다. 합성 SQLite DB는 기존 clarification 흐름을 쓰고, 이 endpoint에서는 404로 처리한다.
- 흐름(모델 호출은 정확히 1회, 재시도·fallback 없음)
  1. drift 검사(`is_postgres_context_current`). 실패하면 404를 돌려준다.
  2. 전체 runtime ontology와 context의 published mapping으로 `generate_ir`를 1회 호출한다. prompt는 매핑된 field만 `available_fields`로 알려준다.
  3. QueryIR 구조 검증에 실패하면 `NOT_UNDERSTOOD`다.
  4. select·filter가 context의 `unsupported_fields()`를 참조하면 실행하지 않고 `UNSUPPORTED`로 답한다. 이때 field의 한국어 label을 함께 보낸다.
  5. 반사 guard: QueryIR 안의 문자열이 질문 원문 전체, database ID, 비의미 물리 table·column 이름과 같으면 `NOT_UNDERSTOOD`다. 질문 전체를 `equals` filter로 복사해 0행이 되는 기존 관찰 경향을 막는다.
  6. 카드 projection: 모델의 filter·entity·limit은 그대로 둔다. select만 **서버 고정 카드 field**(canonical 순서 중 매핑된 field 전체)로 바꾼다. legacy 챗봇이 view의 모든 column을 조회해 카드로 보여주는 것과 같은 조건이다. 이 재작성은 서버 규칙이며 모델 출력에 새 filter를 더하지 않는다.
  7. 재작성한 QueryIR을 기존 `ExecutionHarness`로 실행한다. 고정 출력 model, context compiler·policy, drift 재검사·결과 정규화를 하는 routing executor를 쓴다. 검증 단계 실패(`UNKNOWN_FIELD` 등)는 `NOT_UNDERSTOOD`, 실행 단계 실패는 오류로 처리한다.
- 응답 `adaptive-chat-query-v1`(HTTP 200)
  - `status`: `SUCCEEDED` | `UNSUPPORTED` | `NOT_UNDERSTOOD`
  - `SUCCEEDED`:
    - `columns`: canonical DTO key로, 예: `course_name`, `credits`, `start_time`.
    - `rows`: 결과 행.
    - `row_count`, `limit`.
    - `understood.filters`: field label·연산자·값이다. 질문을 어떻게 이해했는지 SQL 대신 보여주는 용도다.
  - `UNSUPPORTED`: `unsupported`(field·label).
  - `NOT_UNDERSTOOD`: `failure_code`(고정 code).
- 오류 응답 `{"error":{"code"}}`
  - 400 `CHAT_INVALID_REQUEST`
  - 404 `CHAT_CONTEXT_UNAVAILABLE`
  - 503 `CHAT_MODEL_UNAVAILABLE`: prototype에 Ollama가 설정되지 않았거나 모델 호출이 실패한 경우다. timeout 등은 `failure_code`에 담는다.
  - 500 `CHAT_EXECUTION_FAILED`: `failure_code`를 담는다.
- 값은 JSON 안전 타입만 쓴다. `Decimal`은 수로 바꾸고, 그 밖의 타입이 나오면 실행 실패로 닫는다.
- 질문·SQL·물리 이름·행 값·예외 text는 log에 쓰지 않는다.

### 2. prototype 조립

- `create_synthetic_clarification_prototype(...)`가 route를 등록한다. model은 `--ollama-model-tag`로 만든 기존 `OllamaQueryModel`이며, 없으면 503을 돌려준다.
- 기존 route·clarification·catalog 동작은 바꾸지 않는다.

## 제외

- `ChatPopup` 연결·`dev:all` DSN 주입(7-14), legacy와 비교(7-15).
- 다중 턴 대화, clarification 연결, 수강 이력 제외 옵션(legacy의 `excludeCompletedCourses`), 선수과목·강의실 등 15개 밖 field.
- legacy backend·view·DB 변경.

## 보안 계약(유지)

- 모델 호출은 1회이고 raw SQL fallback은 없다.
- QueryIR 검증·compiler·AST policy·read-only executor를 우회하지 않는다.
- 명시적으로 게시된 PostgreSQL profile만 실행하고, server-owned DSN과 drift 검사를 유지한다.
- browser에는 SQL·물리 이름·DSN을 보내지 않는다.
- 카드 projection은 select만 서버 고정 목록으로 바꾸고, filter는 모델 출력 그대로 기존 검증을 받는다.

## 테스트

- fake model(호출 수 기록)과 fake PostgreSQL context로 확인한다.
  - 성공 시 카드 column·행 정규화·understood filter를 확인한다.
  - 모델 호출 1회를 확인한다.
  - 미지원 field는 실행 0회로 `UNSUPPORTED`가 되는지 확인한다.
  - 질문 반사·물리 이름 반사·비JSON·잘못된 구조·허용되지 않은 연산자는 `NOT_UNDERSTOOD`가 되는지 확인한다.
  - drift·unknown·SQLite DB는 404, model 없음·모델 실패는 503, 실행 실패는 500인지 확인한다.
  - 요청 형식(추가 key, 빈 질문, 500자 초과, 중복 key, 비JSON)이 거부되는지 확인한다.
- 응답과 오류에 SQL·물리 이름·DSN·예외 text가 없는지 확인한다.
- 기존 prototype·registry 테스트에 회귀가 없는지 확인한다.
- live(수동): legacy DB와 로컬 Ollama(`text2sql_qwen:latest`, `alpaca-raw-no-system-v1`)로 몇 개 질문을 보낸다. 상태·행 수만 기록하며 모델 성능 주장이 아니다.
