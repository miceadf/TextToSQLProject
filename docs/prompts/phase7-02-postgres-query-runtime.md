# Phase 7-02 PostgreSQL QueryIR 실행 경계 구현 지침

## 목적

Phase 7-01의 PostgreSQL schema inspector와 `dbms-capability-v1` 위에, 기존 DB 독립 QueryIR subset을 PostgreSQL용 parameterized SQL로 컴파일하고 AST allowlist와 read-only executor를 통과시키는 합성 runtime 단위를 구현한다.

이번 단계는 fake psycopg2-style connector로만 검증한다. 실제 PostgreSQL 서버, 실제 CNU 자료, Ollama live 호출, React/HTTP registry 연결은 사용하거나 변경하지 않는다.

## 고정 결정

- 모델 출력은 기존 QueryIR만 허용한다. raw SQL runtime fallback을 추가하지 않는다.
- 지원 subset은 `select`, `filters`, `joins`, `limit`이며 `order_by`, `group_by`, aggregate, subquery, CTE, write는 계속 미지원이다.
- PostgreSQL SQL은 compiler-owned이며 식별자는 double quote escaping, 값과 LIMIT은 psycopg2 `%s` placeholder로만 바인딩한다.
- Phase 7-02 완료 뒤 PostgreSQL capability의 compiler/policy/executor를 `true`로 올리되, 이는 합성 component 준비 상태다. 실제 server 연결이나 UI registry 노출을 뜻하지 않는다.
- SQLite compiler/policy/executor의 SQL, placeholder, fingerprint, 공개 API와 기존 테스트를 깨뜨리지 않는다.
- 모든 PostgreSQL 실행 오류는 고정 code/message로 redaction한다. DSN, host, user, password, SQL, parameter, DB driver exception 원문을 반환·로그하지 않는다.

## 구현 범위

### 1. PostgreSQL compiler

- 기존 `JoinCompilationRequest`, Published Mapping, physical relation graph, deterministic alias/Join plan 계약을 그대로 사용한다.
- PostgreSQL 전용 join-capable compiler와 기존 harness protocol adapter를 추가한다.
- projection alias, table/column identifier는 기존 안전한 double-quote escaping을 재사용한다.
- filter 순서와 LIMIT 순서를 보존하고 placeholder는 정확히 `%s`를 사용한다.
- `contains`는 기존처럼 값만 `%value%`로 바인딩하고 SQL에 literal을 삽입하지 않는다.
- ambiguous/invalid/self join, mapping drift는 기존 고정 Harness code/stage로 fail closed한다.

### 2. PostgreSQL AST policy

- `sqlglot`의 `postgres` dialect로 compiler output을 parse한다.
- 한 개 SELECT, 정확한 projection/table/alias/JOIN predicate/filter/parameterized LIMIT만 허용하는 기존 allowlist 의미를 보존한다.
- `%s`가 sqlglot에서 anonymous placeholder로 정규화되는 경계를 명시적으로 테스트한다.
- comment, multi-statement, write, CTE/subquery/function, extra table/column, literal filter/LIMIT, placeholder/parameter drift를 거부한다.
- SQLite policy 기본 dialect와 회귀를 보존한다.

### 3. PostgreSQL read-only executor

- database ID별 Phase 7-01 `PostgresConnectionConfig`와 주입 가능한 connector를 받는다. production connector는 `psycopg2`를 지연 import한다.
- database ID/config 타입, timeout·max rows 상한을 엄격히 검증한다.
- 연결 metadata에서 PostgreSQL 12 이상을 확인한다.
- transaction을 read-only로 설정하고 statement timeout을 서버 소유 config에서 적용한 뒤 compiler-owned SQL을 tuple parameters로 정확히 한 번 실행한다.
- cursor description 순서를 column 이름으로 사용하고 `max_rows + 1`만 읽어 초과 시 `RESULT_TOO_LARGE`로 거부한다. tuple row와 mapping row를 결정적으로 dict로 변환한다.
- SQLSTATE `57014` 또는 동등한 query-cancel 예외만 `EXECUTION_TIMEOUT`, 나머지는 `EXECUTION_ERROR`로 고정 redaction한다.
- 성공·실패 모두 cursor close, rollback, connection close를 수행하고 cleanup 예외 원문을 노출하지 않는다.
- 미등록 DB는 caller 입력을 반사하지 않는 고정 `DATABASE_NOT_FOUND` 오류로 닫는다.

### 4. 통합·capability

- `ExecutionHarness`가 SQLite 구체 타입 대신 기존 compiler/policy/executor protocol을 정확히 표현하도록 최소 일반화하되 기존 호출과 결과 계약을 바꾸지 않는다.
- PostgreSQL compiler + postgres AST policy + executor를 기존 `ExecutionHarness`에 주입한 fake end-to-end 성공을 검증한다.
- Phase 7-02 완료 상태의 PostgreSQL capability에서 inspector/compiler/policy/executor가 모두 `true`인지 검증한다.
- 이 단계에서 synthetic active DB registry, registration/upload/publish, legacy backend, React를 변경하지 않는다.

## 테스트 범위

- PostgreSQL single/multi-table compiler exact SQL, `%s` count/order, quoting, contains, composite/reverse JOIN
- PostgreSQL AST 정상 SQL과 comment/multi/write/CTE/subquery/function/extra table·column/literal/placeholder drift 거부
- fake connector executor 성공, tuple/mapping row, zero row, exact query/params 1회
- read-only transaction, timeout, server version, max rows, missing DB
- connector/import/cursor/execute/fetch/cleanup/query cancel 실패의 고정 code/stage 및 secret/SQL/parameter redaction
- 기존 harness fake end-to-end PostgreSQL 성공·result schema mismatch
- SQLite join compiler, AST policy, executor, schema-adaptive harness와 Phase 7-01 capability/inspector 회귀

## OpenCode 실행 제한

- 관련 파일과 테스트만 확인한다.
- 관련 backend 테스트만 실행한다.
- 전체 pytest, compileall, 계획/ADR/handoff 갱신, commit, push를 하지 않는다.
- 실제 PostgreSQL/Ollama/CNU 데이터에 연결하지 않는다.
- 긴 diff·전체 로그를 출력하지 않는다. 마지막 보고는 변경 파일, 핵심 계약, 실패 원인, 테스트 요약, 남은 위험만 한국어로 간결히 작성한다.
