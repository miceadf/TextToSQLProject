# Phase 7-04 구현 지침: server-owned PostgreSQL runtime registry 연결

## 목표

Phase 7-03에서 실제 합성 PostgreSQL로 검증한 inspector·mapping·compiler·policy·executor를 기존 adaptive prototype의 database registry, course catalog, clarification 실행 경로에 연결한다. 이번 단계는 backend 조립만 다루며 UI, Ollama, 실제 학교 DB는 사용하지 않는다.

## 범위

- Python server code만 만들 수 있는 strict immutable PostgreSQL profile 계약을 추가한다.
- profile은 `database_id`, 안전한 `label`, `PostgresConnectionConfig`, 명시적으로 승인된 `PublishedMappingProfile`을 가진다.
- `create_synthetic_clarification_prototype()`가 선택적 profile 목록과 테스트용 adapter/executor dependency를 받을 수 있게 한다.
- 기본 인자에서는 PostgreSQL profile이 0개이며 import·app 생성 중 PostgreSQL 연결도 0회다.
- profile이 있으면 startup에서 PostgreSQL catalog를 sample 없이 검사하고 다음을 확인한다.
  - profile·catalog·published mapping의 database ID 일치
  - catalog fingerprint와 mapping fingerprint 일치
  - ontology version 일치
  - course catalog의 canonical 15개 field가 모두 정확히 한 physical column에 mapping됨
  - 모든 mapped table/column이 catalog에 존재
  - physical relation graph 생성 가능
- 검증된 profile만 기존 `/api/adaptive/v1/databases`에 `schema: "postgres"`로 나타난다.
- `/api/adaptive/v1/courses`는 database ID에 따라 기존 SQLite 또는 PostgreSQL compiler·AST policy·read-only executor를 선택한다.
- clarification start/submit도 같은 server-owned mapping과 database별 compiler/policy/executor를 사용한다.
- PostgreSQL context load, catalog query, clarification execute 직전에 catalog를 다시 inspect해 fingerprint drift를 확인한다. drift·연결 실패는 기존 fixed redacted unavailable 오류로 닫는다.

## 보안·격리 계약

- DSN, host, port, user, password, schema, physical table/column, SQL, bound parameter, driver exception text를 HTTP 응답·예외·repr·log·app public state에 노출하지 않는다.
- HTTP request는 PostgreSQL profile, DSN, schema, mapping, catalog, compiler, policy, executor를 주입할 수 없다.
- raw SQL fallback을 추가하지 않는다.
- profile ID는 기존 fixed SQLite ID와 중복될 수 없다. 전체 fixed registry 상한과 deterministic ordering을 유지한다.
- private SQLite activation 경로와 legacy app을 변경하지 않는다.
- PostgreSQL profile을 제공하지 않은 기존 SQLite registry/catalog/clarification 동작과 응답 shape는 그대로 유지한다.
- fail-open, 자동 mapping proposal publish, client 제공 mapping, 실제 CNU 데이터 접근을 금지한다.

## 권장 구조

- PostgreSQL profile validation과 runtime context를 새 작은 backend module로 분리한다.
- compiler/policy/executor는 mapping/database ID로 라우팅하는 얇은 server-owned router를 사용한다.
- 기존 `SyntheticDatabaseRegistry`는 `postgres` entry를 허용하되 credential 객체를 보관하거나 직렬화하지 않는다.
- 테스트 injection은 connector 자체보다 validated adapter/executor 또는 factory 수준으로 제한하고 production 기본값은 기존 `PostgresAdapter`·`PostgresReadOnlyExecutor`를 사용한다.

## 테스트

관련 테스트만 실행한다.

- 기본 앱 생성과 기존 SQLite 경로에서 PostgreSQL dependency 호출 0회
- valid synthetic PostgreSQL profile의 registry listing
- catalog의 PostgreSQL compiler/policy/executor routing과 canonical DTO
- clarification start/submit의 PostgreSQL routing
- startup ID/fingerprint/ontology/mapping/table/column/graph 불일치 거부
- 요청 전 schema drift와 inspector/executor 실패의 fail-closed 처리
- duplicate ID, unknown ID, request profile/DSN/schema/mapping 주입 거부
- 응답·오류·repr에 credential, schema, physical identifier, SQL, driver text 비노출
- 기존 Phase 6-16/19 SQLite registry/catalog/clarification 회귀

실제 PostgreSQL server, Docker, Ollama, frontend test, 전체 pytest, compileall, git commit은 실행하지 않는다.

## 완료 보고

긴 diff와 전체 로그를 출력하지 않는다. 변경 파일, 핵심 계약, 관련 테스트 결과, 실패 원인, 남은 위험만 한국어로 간결히 보고한다. 문서의 계획·ADR·handoff는 수정하지 않는다.
