# Phase 5-18 구현 지침: 직접 SQL baseline arm과 QueryIR paired 비교 계약

## 목표

§15.1 비교 시스템 중 "기존 모델 + 동적 schema prompt → 직접 SQL"(이하 **SQL arm**)을 기존 QueryIR 평가(이하 **IR arm**)와 **같은 합성 질문·같은 fixture·같은 분모**로 비교할 수 있게 한다. 이번 단계는 계약·runner·비교 report를 합성 provider로만 검증한다. live Ollama 실행과 수치 기록은 Phase 5-19로 분리한다.

## 범위

1. 신규 `backend/adaptive/evaluation/direct_sql.py`
   - 동적 schema prompt(`direct-sql-v1`), 출력 추출, raw SQL 정책, 실행, 위치 무관 결과 비교, runner, report.
2. 신규 `backend/adaptive/evaluation/arm_comparison.py`
   - IR arm `EvaluationRunReport`와 SQL arm report의 paired 비교.
3. 신규 `backend/adaptive/evaluation/direct_sql_ollama.py`
   - Phase 7-08 `OllamaStartupConfig`·loopback transport를 재사용하는 SQL 생성 provider(1회 호출).
4. 신규 테스트 `tests/adaptive/test_direct_sql_arm.py`.

기존 IR runner·report 계약, runtime harness·compiler·policy·executor, prototype, frontend, legacy는 변경하지 않는다. `_prepare_fixture_contexts`와 `SQLiteReadOnlyExecutor`는 재사용만 한다.

## 격리 원칙

- raw SQL 실행은 **평가 전용**이다. runtime HTTP·prototype·clarification 경로에 연결하지 않고, 이 모듈을 runtime에서 import하지 않는다(ADR-048/049의 raw SQL fallback 금지 유지).
- 평가 대상은 개발 세트 `fixture_v1_cases.yaml` 40건이다. 독립 holdout 30건(P2-041~070)은 사람 검토·동결 전이므로 이 단계와 5-19에서 실행하지 않는다.
- 모델에는 gold IR·gold 결과를 주지 않는다.

## prompt 계약 (`direct-sql-v1`)

- 입력: fixture의 `DatabaseCatalog`와 질문만. sample 값은 넣지 않는다(IR arm과 같은 조건).
- 결정적 문자열: 테이블 이름순, 각 테이블 `name(col TYPE, ...)`(catalog 선언 순서), PK 목록, FK `table.col -> ref_table.ref_col`.
- 규칙은 도메인 힌트 없이 일반 규칙만: SQLite dialect, 단일 SELECT만, 나열된 table·column만, 이름을 만들지 말 것, SQL만 출력, 판단 불가 시 `UNKNOWN`.
- 템플릿 SHA-256을 report에 기록한다. legacy `backend/prompt.py`의 CNU 전용 규칙은 복사하지 않는다(고정 schema arm은 별도 후보).

## 출력 추출 (`direct-sql-extract-v1`)

결정적이고 보정하지 않는다.

1. 문자열이 아니면 `NON_STRING_OUTPUT`, 공백뿐이면 `EMPTY_OUTPUT`.
2. 정확히 하나의 fenced block(```` ```sql ```` 또는 ```` ``` ````)이 있으면 그 내용만 사용한다. fence가 둘 이상이면 `AMBIGUOUS_OUTPUT`.
3. 앞의 `SQL:` label 한 번과 앞뒤 공백, 끝의 `;`를 제거한다.
4. 결과가 대소문자 무관 `UNKNOWN`이면 `ABSTAIN`.
5. 그 외 문자열은 그대로 정책으로 넘긴다. 설명 문장 제거·키워드 추정 같은 보정은 하지 않는다.

## raw SQL 정책

`sqlglot` sqlite dialect로 검사한다. 실패는 고정 code만 기록한다.

- parse 실패 `SQL_PARSE_ERROR`, statement 수 ≠ 1 `SQL_MULTIPLE_STATEMENTS`.
- root는 `Select` 또는 `Union`/`Intersect`/`Except`만. 그 외 `SQL_NOT_SELECT`.
- 트리 어디에도 DML/DDL/`Command`/`Pragma`/transaction 노드가 없어야 한다(`SQL_FORBIDDEN_NODE`).
- 모든 `Table`은 catalog table이거나 **자기 조상 chain의 WITH가 정의한** CTE 이름이어야 하고 schema/catalog 한정자가 없어야 한다(`SQL_UNKNOWN_TABLE`). 중첩 CTE 이름으로 바깥 참조를 위장할 수 없다. `sqlite_` 접두 table·CTE 이름은 항상 거부한다.
- 정책 통과 뒤에도 기존 `SQLiteReadOnlyExecutor`(`mode=ro`, `query_only`, authorizer, 2초, 200행)를 상속한 평가 전용 executor로만 실행하며, authorizer가 `SQLITE_READ`를 해당 fixture catalog table로 제한한다(방어 2중화). parameter는 없다.

## 결과 비교 (`positional-row-multiset-v1`)

- column 이름은 비교하지 않는다(SQL arm은 physical alias를 쓰므로).
- 결과 column 수는 gold `select` 수와 같아야 한다. 다르면 `COLUMN_COUNT_MISMATCH`.
- 결과 column 이름이 중복되면 dict row가 값을 잃으므로 `RESULT_DUPLICATE_COLUMNS`로 판정 불가 처리한다.
- 각 값 정규화: `None`→`N:`(모든 값과 구별), bool→`V:1`/`V:0`, int·float·Decimal→`V:`+유한 `Decimal` normalize 문자열(3과 3.0 동일), str→`V:`+그대로. 그 외 타입은 비교 불가.
- 각 행을 정규화 값의 정렬 tuple로 만들고 행 multiset을 gold 행(같은 방식, gold select 순서 값)과 비교한다. 행 순서·column 순서는 무시하고 중복 행은 보존한다.
- 한계(문서화): 행 안 값 위치를 무시하므로 같은 값 집합의 column 뒤바뀜은 구분하지 않는다. 문자열 `'3'`과 숫자 3은 같은 값으로 본다.

## SQL arm report (`direct-sql-run-v1`)

- config: `model_run_id`, `model_label`, variants, `oracle_smoke`, prompt·extract·comparison version, 템플릿 SHA-256.
- IR report와 같은 benchmark ID·version·gold set fingerprint·seed fingerprint·fixture catalog fingerprint(variant별)를 기록한다.
- attempt: `case_id`, `variant`, `request_id`(IR arm과 같은 결정적 규칙), `outcome`(`CORRECT`|`INCORRECT_RESULT`|위 고정 code|`PROVIDER_ERROR`|`PROVIDER_TIMEOUT`|`EXECUTION_ERROR`|`EXECUTION_TIMEOUT`|`RESULT_TOO_LARGE`), `sql_fingerprint`(추출 SQL SHA-256, 있을 때), `result_fingerprint`(실행 성공 시).
- 질문·prompt·raw 출력·SQL 원문·행 값은 report에 넣지 않는다.
- 지표(모든 scheduled attempt 분모, ADR-021): `valid_sql`(정책 통과), `execution_success`, `execution_correct`, `abstain`, outcome별 count. 전체와 variant별.

## paired 비교 (`arm-comparison-v1`)

- 두 report의 benchmark ID·version·gold fingerprint·seed fingerprint·variant 집합·variant별 catalog fingerprint·`oracle_smoke`·(case, variant) attempt 집합이 정확히 같지 않으면 비교를 거부한다(ADR-027 확장). 또한 모든 case가 모든 variant를 가진 전체 schedule이어야 하고, 각 report의 전체·variant별 분모와 attempt 수가 일치해야 하며, 각 attempt의 request ID가 그 report의 run ID 기준 결정적 규칙과 같아야 한다.
- 한계: IR arm은 예측 select field 집합이 gold와 같아야 비교 가능(`NOT_COMPARABLE_PROJECTION`)하지만 SQL arm은 column 이름을 비교할 수 없어 column 수와 값만 본다. SQL arm이 이 점에서 약간 관대하다.
- IR arm 정답은 `semantic_correct is True`, SQL arm 정답은 `outcome == CORRECT`.
- 출력: 전체·variant별 `both_correct`, `ir_only`, `sql_only`, `neither`, 각 arm 정답 수와 분모, 두 arm의 `model_label`과 동일 여부, `oracle_smoke` 여부, `gold_human_review_pending`.
- 통계 검정 결과나 우열 결론 문장은 만들지 않는다. discordant count(b=ir_only, c=sql_only)만 기록한다.

## Ollama SQL provider

- `OllamaDirectSqlModel(config: OllamaStartupConfig, *, transport=None)`: Phase 7-08 `build_loopback_transport` 재사용, payload는 `{"model", "prompt", "stream": false, "options": {"temperature": 0}}`, 호출 1회, retry 없음.
- envelope가 dict이고 `error`가 없고 `response`가 문자열이면 그 문자열을 반환한다. 그 외는 `PROVIDER_ERROR`, timeout 계열은 `PROVIDER_TIMEOUT` 고정 예외. 원문 비보존.
- repr에 URL·tag를 넣지 않는다.

## 테스트 (합성 provider만)

- prompt 결정성, table·column·FK 포함, sample·질문 외 원문 비포함, 템플릿 hash 고정.
- 추출 규칙 각 분기.
- 정책: DML/DDL/PRAGMA/ATTACH/다중 statement/`sqlite_master`/schema 한정/unknown table 거부, CTE·JOIN·subquery·UNION 허용.
- **oracle 자기 일관성**: gold IR을 기존 compiler로 컴파일하고 parameter를 안전한 literal로 치환한 SQL을 fake provider가 반환할 때 40×3 = 120 attempt가 모두 `CORRECT`.
- 비교기: column 순서 뒤바뀜·행 순서 무관 정답, 행 누락·중복·추가 column·NULL·3 대 3.0.
- provider 실패·timeout·비문자열이 고정 outcome으로 기록되고 분모에 남음.
- paired 비교: 정상 집계, fingerprint·variant·attempt 집합 불일치 거부.
- Ollama SQL provider: 단일 호출, payload, envelope 오류 분류, 비노출.
- runtime 격리: `backend/adaptive/api`·`harness` 모듈이 `direct_sql`을 import하지 않음.
- 회귀: `tests/adaptive/test_evaluation_runner.py` 등 기존 평가 테스트.

## 금지

- live Ollama 호출, 실제 CNU·학교 DB, holdout 30건 실행.
- runtime 경로에서 raw SQL 실행, SQL 보정·재시도·repair.
- 기존 IR runner·report schema 변경, 성능 주장, 사람 승인 상태 변경.
