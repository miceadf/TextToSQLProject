# Phase 7-03 합성 PostgreSQL 실제 연결 smoke 구현 지침

## 목적

Phase 7-01/02의 PostgreSQL inspector→mapping proposal→physical relation graph→QueryIR compiler→AST policy→read-only executor를 완전 합성 schema/data가 들어 있는 실제 로컬 PostgreSQL에서 한 번 연결 검증하는 독립 smoke를 구현한다.

이번 단계는 backend/CLI 통합 검증만 한다. React·HTTP DB registry·Ollama·legacy 경로는 변경하지 않는다. 실제 CNU 자료나 기존 PostgreSQL database/schema를 읽거나 수정하지 않는다.

## 고정 경계

- 실행에는 명시적 `--live-synthetic`가 필요하다. 기본 실행은 provider/DB 연결·DDL·파일 쓰기 0회로 종료한다.
- DSN은 CLI 인자나 파일이 아니라 server-owned 환경변수 `SCHEMA_ADAPTIVE_SYNTHETIC_POSTGRES_DSN`에서만 읽는다.
- DSN은 `postgresql://` URI, loopback host(`127.0.0.1`, `localhost`, `::1`), database 이름 `schema_adaptive_synthetic`만 허용한다. multi-host, Unix socket, 다른 database, fragment, 알 수 없는 query option은 연결 전에 거부한다.
- 비밀번호·DSN·host/port·생성 schema 이름·SQL·질문·row/sample·driver exception 원문을 stdout/stderr/result/error에 출력하거나 저장하지 않는다.
- 기존 database나 schema를 재사용하지 않는다. 프로세스가 생성한 고유 `schema_adaptive_smoke_<hex>` schema만 사용하고 `finally`에서 그 정확한 schema만 제거한다.
- raw SQL model fallback, 실제 Ollama 호출, actual CNU data, browser/UI, registry 활성화는 없다.

## 구현 범위

### 1. 합성 PostgreSQL fixture

- 기존 `DEFAULT_SEED` 6개 강좌와 normalized SQLite fixture의 논리 구조를 재사용한다.
- PostgreSQL용 departments/instructors/courses/course_offerings/schedules DDL과 parameterized insert를 제공한다.
- identifier는 `psycopg2.sql.Identifier`, 값은 `%s` binding으로만 구성한다. schema 이름을 문자열 보간하지 않는다.
- 생성 schema는 프로세스 내부에서 cryptographic random suffix로 만들고 엄격한 prefix/pattern을 재검증한다.
- bootstrap connection은 synthetic 전용 database에서 schema 생성·seed·cleanup에만 사용한다. 각 단계 실패는 고정 오류로 redaction한다.
- fixture setup 뒤 commit하고, cleanup은 성공·실패 모두 별도 안전 경계에서 `DROP SCHEMA <exact generated> CASCADE` 후 commit한다. 생성 전 실패에는 drop하지 않는다.

### 2. live smoke orchestration

- 작은 module CLI(권장: `python -m backend.adaptive.harness.postgres_live_smoke`)를 추가한다.
- preflight가 flag·환경변수·URI·loopback·database·query option을 모두 검증한 뒤에만 psycopg2를 지연 import하고 첫 연결을 수행한다.
- 생성 schema를 Phase 7-01 `PostgresConnectionConfig`에 server-owned로 넣어 `PostgresAdapter.inspect()`를 실행한다.
- catalog가 `postgres`, sample 0개, 예상 table/PK/FK 구조, PostgreSQL 12+를 만족하는지 확인한다.
- `RuleBasedSchemaAligner`와 default ontology로 proposal을 생성하고, smoke에 필요한 semantic field의 상위 후보가 예상 physical field인지 확인한다. 자동 publish로 과장하지 않는다.
- 확인된 합성 expected mapping으로 메모리 `PublishedMappingProfile`을 만들고 catalog fingerprint와 ontology version을 결합한다.
- physical graph, `SchemaAdaptivePostgresCompiler`, `SchemaAdaptivePostgresPolicy`, `PostgresReadOnlyExecutor`, 기존 `ExecutionHarness`를 연결한다.
- `FakeModel`의 고정 QueryIR로 최소 두 query를 실행한다: multi-table join/filter 성공과 0-row 성공. 결과는 `DEFAULT_SEED`의 기대값과 내부 비교한다.
- 성공 출력은 version/status, table·mapping·query count, schema fingerprint 정도의 안전한 JSON 한 건만 허용한다. row/question/SQL/schema/DSN은 출력하지 않는다.
- 실패 출력은 고정 code만 사용하고 exit code는 0/2처럼 결정적으로 구분한다. traceback과 원문 예외를 출력하지 않는다.

### 3. 검증 가능 구조

- DSN/preflight, fixture setup/cleanup, orchestration은 connector/clock/token 또는 필요한 최소 dependency를 주입 가능하게 해 unit test가 실제 network 없이 경계를 검증할 수 있게 한다.
- 실제 live test는 명시적 환경변수와 flag가 있을 때만 실행하고 기본 pytest에서는 skip하거나 network 0회 unit test만 수행한다.
- live smoke 중 생성한 schema가 성공·중간 실패 뒤 남지 않는지 실제 PostgreSQL에서 메인 Codex가 별도 확인할 수 있게 한다. 단, 공개 출력에는 schema 이름을 넣지 않는다.

## 테스트 범위

- no-flag/no-env/invalid scheme·host·database·multi-host·query option의 연결 0회 거부
- DSN·password·host·port·schema·SQL·row·driver error redaction
- generated schema prefix/pattern 및 identifier composition, parameterized inserts
- setup 성공/부분 실패/inspection/mapping/query 실패의 cleanup 정확히 1회, 생성 전 실패 cleanup 0회
- catalog expected tables, samples 0, PK/FK/composite order
- mapping top candidate 검증과 임의 auto-publish 없음
- 두 QueryIR의 compiler→policy→executor 결과 내부 비교 및 안전한 summary
- 기존 Phase 7-01/02 fake tests와 SQLite compiler/policy/executor 회귀

## OpenCode 실행 제한

- 관련 파일과 테스트만 확인한다.
- unit/fake 관련 backend 테스트만 실행한다.
- 실제 PostgreSQL container/server를 시작하거나 연결하지 않는다. live smoke는 메인 Codex가 구현 검토 뒤 수행한다.
- 전체 pytest, compileall, 계획/ADR/handoff 갱신, commit, push를 하지 않는다.
- legacy, React, HTTP registry, Ollama를 변경하지 않는다.
- 긴 diff·전체 로그를 출력하지 않는다. 마지막 보고는 변경 파일, 핵심 계약, 실패 원인, 테스트 요약, 남은 위험만 한국어로 간결히 작성한다.
