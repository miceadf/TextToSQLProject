# Phase 9-10 보완 지침 2: Luna 리뷰 확인 지적 고침

Luna(`gpt-6-luna` high) 리뷰와 Claude 검토에서 나와 Claude가 재현한 4건만 고친다. 9-10 지침의 "지킬 것"은 그대로다(live 모델 호출 없음, 사용자 서버 안 건드림, 커밋 없음, `.env` 값 출력 없음, 전체 pytest 안 돌림). 각 고침에 회귀 테스트를 둔다.

## 1. 함수 정책 구멍 (High, 재현됨)

- `direct_sql_pg.check_sql`은 위험 함수 이름만 막는다. `SELECT public.leak_code() AS course_code FROM courses`(sqlglot: `Dot` + `Anonymous:leak_code`)와 `SELECT leak_code() ...`가 통과한다. 사용자 정의 함수는 다른 schema를 읽거나 부작용을 낼 수 있다.
- 고침:
  - schema로 한정한 함수 호출(`exp.Dot` 안의 함수)은 모두 거절한다.
  - `exp.Anonymous`(sqlglot이 모르는 함수)는 코드에 고정한 PostgreSQL 내장 함수 허용 목록에 있을 때만 허용한다. 목록은 글자·날짜·숫자·배열 처리에 흔한 내장 함수로 좁게 잡는다(예: `split_part`, `string_to_array`, `regexp_split_to_array`, `array_to_string`, `unnest`, `btrim`, `initcap`, `to_char`, `date_part`). sqlglot이 아는 함수(`Count`, `Lower` 등 typed 노드)는 기존 금지 이름 검사만 한다.
  - 거절 코드는 기존 `SQL_FORBIDDEN_NODE`.
- 테스트: 위 두 SQL 거절, 허용 목록 함수와 `count`/`lower` 허용.

## 2. 200행 감싸기와 끝 주석 (재현됨)

- `SELECT code AS course_code FROM courses -- c`를 `SELECT * FROM (<sql>) AS direct_sql_result LIMIT 200`으로 감싸면 닫는 괄호가 주석에 들어가 실행 오류가 된다.
- 고침: `"SELECT * FROM (\n" + sql + "\n) AS direct_sql_result LIMIT 200"`처럼 원래 SQL 앞뒤에 줄바꿈을 둔다.
- 테스트: 끝에 `--` 주석이 있는 SQL을 감싼 결과에서 닫는 괄호가 주석 밖에 있음.

## 3. 거절 채점의 arm 간 차이 (Medium, 재현됨)

- `direct_sql_pg` CLI의 legacy 채점은 `status == "UNSUPPORTED"`만 거절로 본다. 직접 SQL의 `UNKNOWN`은 `UNSUPPORTED`가 되어 `REFUSED`(인정)인데, QueryIR의 `NOT_UNDERSTOOD`는 `NO_ANSWER`가 된다. 직접 SQL은 두 경우를 구분할 수 없으므로 같은 질문에서 직접 SQL만 유리하다.
- 고침: 이 CLI의 legacy 채점에서 두 arm 모두 `blind_eval.DECLINE_STATUSES`(`UNSUPPORTED`, `NOT_UNDERSTOOD`)를 거절로 본다. `blind_eval`과 같은 규칙이다. `legacy_chat_compare` 자체 채점(9-08 수치의 기준)은 바꾸지 않는다.
- 문서: 15.1.1에 "이 CLI의 legacy 거절 규칙은 `blind_eval`과 같고, 9-08 `legacy_chat_compare` 수치와 직접 비교하지 않는다"를 한 줄 적는다.
- 테스트: 거절 대상 질문(scope `UNSUPPORTED_FIELD`)에서 QueryIR `NOT_UNDERSTOOD`와 직접 SQL `UNKNOWN`이 같은 결과.

## 4. blind 지연 측정 위치 (Low)

- `blind_eval.run`이 `elapsed_ms`를 채점 뒤에 잰다. `ask` 반환 직후(채점 전)에 재도록 옮긴다. 답에 `elapsed_ms`가 이미 있으면 그 값을 쓴다(직접 SQL과 같은 기준).
- 테스트: 채점 시간이 지연에 들어가지 않음(가짜 시계나 순서 확인).

## 확인

- `tests/adaptive/test_direct_sql_pg.py`, `test_blind_eval`, `test_legacy_chat_compare`만 실행한다.
- 변경 이력의 9-10b 항목 아래에 "9-10c Luna 지적 4건 고침"을 한 줄 더한다(로드맵의 Luna 리뷰 항목은 완료로).
- 끝나면 바꾼 파일과 테스트 결과를 짧게 적는다.
