# Phase 9-10 지침: PostgreSQL 직접 SQL 비교군 (평가 전용)

## 배경

- 사용자의 원래 의도는 "등록한 DB의 실제 구조와 데이터 모양을 보고 모델이 SQL을 만든다"였다. 지금 제품은 공통 항목 mapping → QueryIR → compiler 구조다.
- 두 방식을 같은 조건에서 비교해, 제품 구조는 결과를 본 뒤 사용자가 정한다.
- 지금 직접 SQL 비교군(`backend/adaptive/evaluation/direct_sql.py`, Phase 5-19~22)은 쓸 수 없다.
  - SQLite 합성 fixture에서만 돈다(`_CatalogBoundExecutor`). 제품은 PostgreSQL 전용이다(ADR-089).
  - prompt에 table·column·선언형·PK/FK만 있다. 예시 값과 값 힌트가 없다.
  - 채점이 gold 행 위치 비교라 column이 하나만 더 있어도 틀린다.
  - QueryIR 쪽은 정답 mapping(`fixture_published_mapping`)을 받았다.
- 2026-10-12 사용자 결정(모두 제안대로):
  1. 9-10을 진행한다.
  2. 직접 SQL 쪽 context: table·column·자료형·PK/FK + column마다 예시 값 10개까지 + 값 힌트.
  3. OpenAI로도 직접 SQL 쪽을 돌릴 수 있다. 이때 실제 table·column 이름과 예시 값이 외부로 나간다. 평가 전용 범위에서 ADR-099의 "물리 이름·DB 행을 보내지 않음"을 넓히는 사용자 결정이다. 제품 챗봇의 `openai` 선택지가 보내는 것은 그대로다.
  4. 개발용 비교 질문: 레거시 30, `other_school` 12, `rehearsal_school` 14(합 56).

## 지킬 것 (바꾸지 않음)

- 평가 전용이다. runtime HTTP·prototype·명확화·챗봇 코드는 새 모듈을 import하지 않는다(ADR-048/049 raw SQL fallback 금지). 이를 확인하는 테스트를 둔다(기존 `direct_sql` import 금지 테스트가 있으면 같은 방식으로 넓힌다).
- 화면, 제품 API, 기존 QueryIR 경로, 기존 SQLite `direct_sql.py` 동작은 바꾸지 않는다. 기존 테스트가 그대로 통과해야 한다.
- legacy DB(postgres, public schema)는 읽기 전용이다. DDL·쓰기 없음. 테스트용 schema는 합성 DB `schema_adaptive_synthetic`에만 만든다.
- DSN·비밀번호·API 키는 출력·기록·repr·오류에 나가지 않는다. `backend/.env` 값을 출력하지 않는다.
- 기록에는 SQL 원문, 모델 원문, 결과 행 값, prompt를 남기지 않는다. 결과 코드, 개수, fingerprint(sha256), 시간만 남긴다. (콘솔에 질문 id별 결과 코드는 출력해도 된다.)
- 질문당 모델 호출 한 번, 재시도 없음.
- 공통 ontology는 바꾸지 않는다.
- 사용자 서버(8000·8123·5173)를 건드리지 않는다. 실제 모델 호출(live 측정)은 하지 않는다. 테스트는 가짜 모델·가짜 전송으로 한다. 합성 DB를 쓰는 기존 live 테스트 방식이 있으면 그 방식만 따른다.
- 커밋하지 않는다. 사용자의 `package.json`, `package-lock.json`, `.claude/`는 건드리지 않는다.

## 만들 것

### 1. `backend/adaptive/evaluation/direct_sql_pg.py`

**context 만들기** (등록된 DB 하나, `database_id`로)
- 저장된 등록 정보에서 catalog(table·column·자료형·PK/FK, schema 이름)와 연결 설정을 읽는다. 등록·런타임이 이미 쓰는 loader를 재사용한다. 관리자 mapping·값 사전·변환은 쓰지 않는다(직접 SQL 쪽은 등록만 한 상태를 가정).
- 예시 값: `PostgresDistinctValueReader`의 `sample=True` 경로로 column마다 최대 10개. 값 하나는 40자에서 자른다. 숫자·날짜·시간 column도 포함한다. 읽을 수 없는 column은 건너뛴다.
- 값 힌트(물리 버전): 질문 단어를 낮은 cardinality 글자 column의 실제 값과 맞춘다. `api/value_hints.py`의 `ValueIndex` 일치 방식(전체 값, 값의 단어 조각, 가까운 철자)을 재사용하되 **관리자 값 사전(약어)은 넣지 않는다**. 힌트는 `"<질문 단어>" -> <schema>.<table>.<column> = '<값>'` 꼴로 보인다. 개수 상한은 QueryIR 쪽 값 힌트와 같게 한다.
- context는 결정적(정렬 고정)이어야 한다.

**prompt `direct-sql-pg-v1`** (영어, 기존 `DIRECT_SQL_TEMPLATE` 문체)
- "PostgreSQL database that stores a university course catalog."
- schema-qualified table 이름, column(자료형), PK/FK, column별 예시 값, 값 힌트.
- 규칙: 읽기 전용 SELECT 하나; 목록의 table·column만; 과목 코드 column을 첫 column으로 `AS course_code`로; 분반 column이 있으면 `AS section`으로 함께; 질문이 말한 조건만; 답할 수 없으면 `UNKNOWN`; SQL만, Markdown·설명 없이.
- 질문은 `build_direct_sql_prompt`처럼 분할 치환(중괄호 해석 금지). template sha256을 기록한다.

**SQL 검사** (PostgreSQL 방언, sqlglot `read="postgres"`)
- `extract_sql`과 결과 코드 체계를 재사용한다.
- 문장 하나, 루트는 Select/Union/Intersect/Except, 쓰기·DDL·SET·COPY·트랜잭션 노드 금지.
- table은 그 등록의 schema에 있는 catalog table이거나 자기 CTE만. schema 생략은 그 등록 schema로 본다. `pg_catalog`, `information_schema`, 다른 schema 금지.
- 위험 함수 금지: 이름이 `pg_`·`lo_`·`dblink`로 시작하는 함수, `current_setting`, `set_config`, `query_to_xml` 계열, `txid_`·`nextval`·`setval`. 이름 비교는 대소문자 무시.

**실행**
- `PostgresReadOnlyExecutor`(READ ONLY 트랜잭션, statement timeout)를 그 등록의 연결 설정으로 쓴다. 행 상한은 챗봇과 같은 200.

**답 모양**
- 기존 채점기가 그대로 쓰도록 챗봇 응답과 같은 dict를 만든다: `status`(`SUCCEEDED` / 거절은 `UNSUPPORTED` / 실패는 `FAILED`), `rows`(`course_code`, 있으면 `section`), `failure_code`(직접 SQL 결과 코드).
- `course_code` column이 없으면 `FAILED` + 고정 코드.

**모델**
- `general`: `CHAT_MODEL` 또는 `qwen2.5-coder:7b-instruct`. 기존 loopback Ollama 전송과 `GENERATION_OPTIONS`를 재사용한다(`direct_sql_ollama.py` 참고). 모델 이름 결정은 `api/chat_models.py`와 같은 규칙.
- `openai`: `OPENAI_MODEL` 또는 `gpt-5-mini`, `backend/.env`의 `OPENAI_API_KEY`. `harness/openai_chat.py`의 고정 HTTPS 주소·proxy/redirect 없음·응답 상한·`finish_reason == "stop"`·고정 실패 코드를 재사용한다. `response_format`은 쓰지 않고 SQL 글자를 받는다. 키는 header에만.
- 모델 id와 이름은 기록한다.

### 2. 실행 CLI

`python -m backend.adaptive.evaluation.direct_sql_pg run --set <legacy|other_school|blind:<eval_id>> --database <database_id> --model <general|openai> [--out <dir>]`

- `legacy`: `legacy_chat_compare`의 질문 세트·기준 SQL 키·`score_case`로 채점한다(QueryIR 쪽과 같은 채점).
- `other_school`: `fixtures/other_school.py`의 `QUESTIONS`와 `grade`.
- `blind:<eval_id>`: `blind_eval`의 `load_questions`와 `grade`. 이 질문 파일은 팀원 DB에도 쓰이므로 `blind_eval`의 잠금 규칙(첫 실행 hash 고정, 덮어쓰지 않음)을 따른다. 직접 SQL 실행은 `blind_eval` run 기록과 같은 디렉터리에 같은 번호 체계로 남기고 `arm: "direct-sql"`을 적는다. 기존 기록은 `arm`이 없으면 QueryIR로 읽는다. `report`는 arm별로 나눠 보인다.
- 질문마다 `elapsed_ms`(모델 호출 + 실행)를 기록한다.
- 요약: 맞음, 조용한 오답(`SUCCEEDED`인데 틀림), 생성 실패(빈 출력·파싱), 정책 거절, 실행 실패, 거절(UNKNOWN)이 맞음/틀림, 상한 도달, 지연 중앙값·p90.
- 기록은 Git이 무시하는 `.schema-adaptive/` 아래에 둔다.

### 3. QueryIR 쪽 지연 기록

- `blind_eval run`과 `legacy_chat_compare`의 live 실행 기록에 질문별 `elapsed_ms`를 더한다(HTTP 왕복). 다른 필드와 채점은 바꾸지 않는다.
- `other_school` 12문항을 QueryIR 쪽으로 돌리는 기존 경로를 찾아(9-08 §3.3) 같은 요약 지표가 나오는지 확인한다. 경로가 없으면 위 CLI에 `--arm queryir`(챗봇 HTTP API 사용, `--base-url` 필요)를 더해 같은 채점기로 돌린다.

## 확인 (테스트)

- 가짜 모델·가짜 reader·가짜 executor로:
  - context가 결정적이고, 예시 값 10개·40자 제한, 값 사전 미사용, schema-qualified 이름.
  - prompt 분할 치환, template sha256 고정.
  - 검사: 쓰기·DDL·여러 문장·다른 schema·`pg_catalog`·위험 함수 거절, CTE·schema 생략 허용.
  - 답 dict가 세 채점기에서 그대로 채점됨(맞음, 조용한 오답, UNKNOWN 거절, `course_code` 없음).
  - OpenAI 전송: 주소·header·모델·`response_format` 없음, 키가 기록·repr·오류에 없음, `finish_reason` 확인.
  - 기록에 SQL·행·prompt·키가 없음.
  - `blind_eval` 잠금·번호·`arm` 읽기 호환.
  - runtime 모듈이 `direct_sql_pg`를 import하지 않음.
- 새 테스트와 관련 기존 테스트(`test_direct_sql_arm`, `test_arm_live_cli`, `blind_eval`, `legacy_chat_compare`, `openai_chat` 관련)만 실행한다. 전체 pytest는 돌리지 않는다.

## 문서

`AGENTS.md` 규칙대로:
- `docs/schema-adaptive-text-to-sql-plan.md`:
  - ADR-100 추가(위 사용자 결정 1~4, 평가 전용, 보내는 데이터 범위, 값 사전 미사용 이유: 직접 SQL 쪽은 "등록만" 비용을 재기 위함).
  - 15절(평가 설계)에 9-10 비교 조건과 지표, 17절에 OpenAI 평가 전용 전송 범위.
  - 로드맵에 9-10 항목, 변경 이력에 2026-10-12 항목. live 측정은 "사용자 승인 뒤 한 번" 대기로 적는다.
- `docs/next-session-handoff.md` 현재 기준 문단에 9-10 한 줄.
- `docs/human-validation-checkpoints.md`에 9-10 메모: 사용자 결정이며, 개발 질문 56개는 QueryIR 규칙을 맞춘 질문이라 A 쪽에 유리하고, 판정 근거는 팀원 DB 첫 실행이다. 어떤 관문도 승인하지 않는다.

## 끝나면

- 바꾼 파일 목록, 실행한 테스트와 결과, 남은 한계를 짧게 적는다.
