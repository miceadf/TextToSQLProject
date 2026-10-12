# Phase 9-10 보완 지침: 검토 지적 고침과 OpenAI 제외

9-10 구현(`backend/adaptive/evaluation/direct_sql_pg.py` 등, 미커밋)에 대한 Claude 검토 결과다. 아래만 고친다. 다른 동작은 바꾸지 않는다. 9-10 지침(`docs/prompts/phase9-10-direct-sql-postgres.md`)의 "지킬 것"은 그대로다(live 모델 호출 없음, 사용자 서버 안 건드림, 커밋 없음, `.env` 값 출력 없음, 전체 pytest 안 돌림).

## 1. OpenAI 직접 SQL 경로 제거 (2026-10-12 사용자 결정)

- 사용자는 ChatGPT Plus만 쓰고 API 추가 비용을 원하지 않는다. OpenAI를 이용한 측정은 모두 제외한다. 비교는 로컬 `general` 모델로만 한다.
- `OpenAIDirectSqlModel`, `make_model`의 `openai` 분기, 관련 테스트를 지운다.
- CLI `--model`: `direct-sql` arm은 `general`만, `queryir` arm은 `general`과 `legacy-tuned`만 받는다. `openai`는 거절한다(테스트).
- 제품 챗봇의 9-09 `openai` 선택지(ADR-099)는 그대로 둔다.
- 문서:
  - ADR-100의 사용자 결정 3을 "같은 날 사용자가 철회: 추가 비용 없음(ChatGPT Plus는 API 사용량을 포함하지 않음). OpenAI 측정 제외, 직접 SQL OpenAI 경로 제거"로 고친다. 결정·이유 문장에서 OpenAI 전송 내용을 지운다.
  - 17절의 9-10 OpenAI 전송 예외 항목을 지우거나 "철회"로 바꾼다.
  - 15.1.1의 CLI 예시와 `human-validation-checkpoints.md` 9-10 메모, `next-session-handoff.md` 한 줄에서 OpenAI를 지우고 철회를 적는다.

## 2. 행 상한 공정성 (확인된 결함)

- 현재 직접 SQL 결과가 200행을 넘으면 executor가 `RESULT_TOO_LARGE`를 내고 `FAILED`가 된다. 챗봇(QueryIR)은 200행까지 보여 주고 `row_count`·`limit`으로 상한 도달을 알린다. 레거시 채점은 상한 도달을 `CORRECT_SUBSET`으로 인정하고, `blind_eval`은 `CAPPED`로 센다. 그래서 큰 결과 질문에서 직접 SQL만 불리하다.
- 고침: 정책 검사를 통과한 SQL을 실행할 때 `SELECT * FROM (<sql>) AS direct_sql_result LIMIT 200`으로 감싸 최대 200행을 받는다. 200행이면 `SUCCEEDED`, `row_count` 200, `limit` 200(챗봇과 같은 모양). 감싸기는 검사 뒤에만 하고, 검사는 원래 SQL로 한다. `sql_sha256`은 원래 SQL의 것을 유지한다.
- 회귀 테스트: 가짜 executor가 감싼 SQL을 받는지; 200행 결과가 레거시 채점에서 기준의 부분 집합이면 `CORRECT_SUBSET`(인정)으로, `blind_eval` 채점에서 `CAPPED`로 되는지; 199행은 그대로 채점되는지.

## 3. 따옴표 없는 이름의 대소문자 (확인된 결함)

- `check_sql`은 table·schema 이름을 대소문자 그대로 비교한다. PostgreSQL은 따옴표 없는 이름을 소문자로 접으므로, 모델이 `FROM Courses`처럼 쓰면 PostgreSQL에서는 맞는 SQL인데 `SQL_UNKNOWN_TABLE`로 거절된다.
- 고침: identifier가 따옴표 없으면 소문자로 접은 뒤 비교하고, 따옴표가 있으면 그대로 비교한다(schema·table·CTE 모두).
- 회귀 테스트: 따옴표 없는 대문자 table·schema 허용, 따옴표 있는 다른 대소문자는 거절.

## 확인

- `tests/adaptive/test_direct_sql_pg.py`와 9-10에서 돌린 관련 테스트 파일만 실행한다.
- 끝나면 바꾼 파일, 테스트 결과를 짧게 적는다.
