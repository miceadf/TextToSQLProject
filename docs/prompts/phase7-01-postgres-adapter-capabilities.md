# Phase 7-01 PostgreSQL adapter와 capability 계약 구현 지침

## 목적

기존 SQLite schema-adaptive 경계를 보존하면서, 시스템이 지원하는 DBMS와 QueryIR 기능을 명시하는 공통 capability 계약 및 PostgreSQL read-only schema inspector를 한 개의 완결된 합성 개발 단위로 구현한다.

이번 단계는 실제 CNU DB, 실제 PostgreSQL 서버, Ollama live 호출을 사용하지 않는다. PostgreSQL compiler/policy/executor, runtime 질의 API, React UI, connection profile 영속 저장은 후속 단계다.

## 고정 결정

- 초기 지원 DBMS는 `sqlite`, `postgres`뿐이다. Oracle/MySQL은 미지원으로 명시한다.
- 모델 출력의 장기 경계는 DB 독립적 QueryIR이다. raw SQL runtime fallback은 추가하지 않는다.
- DBMS별 adapter는 schema inspection, compiler, policy, executor capability를 명시해야 한다.
- SQLite의 기존 catalog JSON, schema fingerprint, mapping proposal, registration strict reload를 변경하지 않는다.
- PostgreSQL은 server-owned 설정과 단일 허용 schema만 검사한다. 기본 schema는 `public`이다.
- sample 수집은 기본 비활성화이며 이번 PostgreSQL adapter에서는 `include_samples=True`도 명시적으로 거부한다.
- 오류·반환값에 DSN, host, port, user, password, SQL 원문, exception 원문을 노출하지 않는다.

## 구현 범위

### 1. DBMS capability 계약

- versioned frozen Pydantic 계약을 추가한다.
- 최소 필드:
  - adapter/dialect: `sqlite` 또는 `postgres`
  - schema inspection 지원 여부
  - compiler/policy/executor 지원 여부
  - 지원 QueryIR 기능: select, filters, joins, limit
  - 미지원 기능: order_by, group_by, aggregate, subquery, cte, write
  - read-only only, parameterized only
  - max rows 및 timeout 상한
- SQLite 현재 구현 capability와 PostgreSQL Phase 7-01 capability를 각각 결정적으로 반환한다.
- PostgreSQL은 이번 단계에서 inspector만 `true`, compiler/policy/executor는 `false`다.
- 알 수 없는 adapter/dialect는 fallback하지 않고 고정 오류로 거부한다.

### 2. PostgreSQL schema adapter

- 기존 `DatabaseAdapter.inspect(database_id, include_samples=False)` protocol을 구현한다.
- constructor는 database ID별 server-owned connection config와 주입 가능한 connector를 받는다.
- production connector는 기존 설치 dependency인 `psycopg2`를 지연 import한다. import·연결 실패를 고정 catalog 오류로 변환한다.
- config는 최소 DSN과 허용 schema를 가지되 repr/str/오류에 DSN을 노출하지 않는다. database ID·schema 이름은 엄격히 검증한다.
- connect timeout과 statement timeout 상한을 강제하고, inspection transaction을 read-only로 설정한다.
- `pg_catalog`의 parameterized metadata query만 사용해 ordinary/partitioned table, column 순서·declared type·nullable·default·generated/identity, PK 순서, FK와 composite FK sequence를 수집한다.
- system schema, view/materialized view, foreign table, 다른 schema를 제외한다.
- table/column/constraint 결과는 결정적으로 정렬한다.
- PostgreSQL type을 기존 mapping aligner가 이해하는 논리 affinity로 보수적으로 변환한다. SQLite 공개 API와 기존 affinity serialization은 깨뜨리지 않는다.
- catalog dialect는 `postgres`이며 fingerprint는 sample/연결정보를 제외한 schema 구조에서 결정적으로 계산한다.
- cursor/connection은 성공·실패 모두 닫고 rollback/cleanup 실패 원문을 노출하지 않는다.

### 3. 안전·호환성

- legacy `backend/main.py`, `backend/db.py`, 기존 Ollama/SQL 생성 경로를 수정하지 않는다.
- 기존 SQLite adapter 동작·fingerprint·테스트를 그대로 통과시킨다.
- DSN을 public model, catalog, capability, exception message, 로그에 넣지 않는다.
- caller SQL, 임의 metadata query, 여러 schema, sample 값 수집을 허용하지 않는다.
- PostgreSQL server major version은 연결 metadata로 확인하되 이번 단계에서 지원 범위를 과장하지 않는다. synthetic fake connector 결과만 검증했음을 문서화한다.

## 테스트 범위

- capability exact contract, sqlite/postgres 값, unknown dialect fail-closed
- fake psycopg2-style connection/cursor로 PostgreSQL catalog round-trip
- 단일 schema 격리, system/view 제외, table/column 순서 결정성
- PK·composite FK sequence, generated/identity, type affinity
- parameterized metadata query와 read-only/timeout 설정
- unknown database ID, invalid config/schema, connector/import/query failure의 고정 오류
- cleanup 성공·실패, DSN/password/SQL/exception redaction
- `include_samples=True` 차단 및 query 0회
- 기존 SQLite catalog/fingerprint, mapping aligner, relation planning 회귀

## OpenCode 실행 제한

- 관련 파일과 테스트만 확인한다.
- 관련 backend 테스트만 실행한다.
- 전체 pytest, compileall, 문서 계획/ADR/handoff 갱신, commit, push를 하지 않는다.
- 실제 PostgreSQL/Ollama/CNU 데이터에 연결하지 않는다.
- 마지막 보고는 변경 파일, 핵심 계약, 실패 원인, 관련 테스트 요약, 남은 위험만 한국어로 간결히 작성한다.
