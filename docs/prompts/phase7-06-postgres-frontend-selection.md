# Phase 7-06 구현 지침: PostgreSQL frontend 선택 연결

## 목표

Phase 7-05 CLI startup에서 strict load·검증되어 Phase 7-04 registry에 노출된 PostgreSQL profile을 메인 `DB 변경`과 `/adaptive-prototype` 흐름에서 선택할 수 있게 한다. frontend는 registry의 안전한 `database_id`·`label`·`schema=postgres`만 신뢰하며, 선택한 동일 database ID를 기존 course catalog와 clarification start에 전달한다.

이번 단계는 frontend registry/selection 계약만 완결한다. backend profile loader·runtime, private artifact, DSN, mapping, SQL compiler/policy/executor, SQLite upload/review/publish, Ollama provider, 실제 CNU/학교 DB, legacy API는 변경하거나 사용하지 않는다.

## 구현 범위

1. `src/adaptive-prototype/databases.ts`의 strict registry 계약에 `postgres` schema variant를 추가한다.
2. 기존 `RegistryDatabase` union과 parser가 `flat | legacy | private | postgres`만 허용하도록 한다. 알 수 없는 schema는 계속 전체 registry fail-closed로 거부한다.
3. PostgreSQL label은 Phase 7-04 backend의 안전한 ASCII label 계약과 같은 제한을 적용한다. `private`의 한국어 가능 label 예외를 PostgreSQL에 확대하지 않는다.
4. 메인 `DB 변경` modal은 server-owned registry 항목만 렌더링한다. PostgreSQL entry는 다른 실행 가능 DB처럼 radio 선택·명시적 `적용` 후 활성화한다.
5. 선택 적용 시 기존 Phase 6-16 상태 초기화 계약을 그대로 유지한다: 이전 catalog request abort/stale 무효화, 선택 강의·경고·hover·modal/course 상태 초기화, sessionStorage에는 검증된 database ID만 저장한다.
6. 활성 PostgreSQL ID를 course catalog query와 `/adaptive-prototype?database=...` 링크에 전달한다. prototype page는 registry를 다시 strict load하고 URL ID가 실제 registry에 있을 때만 같은 ID로 clarification start를 보낸다.
7. registry refresh에서 저장된 PostgreSQL ID가 사라지거나 registry가 invalid/unavailable이면 legacy 기준선으로 fail closed한다.

## 보안·노출 계약

- browser가 허용하거나 표시할 PostgreSQL 정보는 registry의 exact `database_id`, 안전한 `label`, literal `schema=postgres`뿐이다.
- DSN, DSN 환경변수 이름, host, port, username, password, 물리 schema/table/column, mapping, fingerprint, SQL, driver 오류를 새 response·state·storage·URL·DOM·log에 추가하지 않는다.
- frontend가 PostgreSQL profile을 생성·수정·등록하거나 임의 DSN/path를 전송하는 기능을 추가하지 않는다.
- request body/query에는 기존 검증된 database ID만 포함한다. raw SQL fallback이나 dialect 선택 입력을 추가하지 않는다.
- server error body/detail을 반사하지 않는 기존 redaction을 유지한다.

## UI 계약

- 기존 registry 순서와 radio rendering을 보존한다. frontend 하드코딩 PostgreSQL entry를 만들지 않는다.
- 사용자는 registry에 실제 존재하는 PostgreSQL entry만 선택할 수 있다.
- `적용` 전에는 active DB, sessionStorage, catalog, clarification link가 바뀌지 않는다.
- 적용 후 current DB label과 catalog/clarification link는 동일 PostgreSQL ID를 사용한다.
- 별도 upload/review UI나 credential 입력란을 만들지 않는다.
- 필요하면 schema 표시를 사람이 읽기 쉬운 안전한 label로 바꿀 수 있지만, raw server metadata를 추가 표시하지 않는다.

## 테스트

관련 frontend 테스트만 실행한다. 실제 PostgreSQL/Ollama/network/browser를 사용하지 않는다.

- strict registry parser가 정상 `postgres` entry를 수락한다.
- extra key, unknown schema, invalid ID, unsafe/Unicode/control/blank/too-long PostgreSQL label, duplicate ID를 거부한다.
- `private` label의 기존 한국어 허용과 flat/legacy 계약은 회귀하지 않는다.
- 저장된 PostgreSQL ID가 현재 registry에 있을 때 복원되고, 사라지면 legacy로 fallback한다.
- write/read sessionStorage가 검증된 PostgreSQL ID만 보존한다.
- catalog client가 PostgreSQL ID를 query parameter로 보내고 echoed ID 불일치를 거부한다.
- clarification client가 동일 PostgreSQL ID를 body에 보낸다.
- prototype URL parser와 registry membership 경계가 invalid/unknown ID를 실행에 사용하지 않는다.
- MainPage가 registry 기반 radio rendering, 명시적 적용, 상태 초기화, 동일 ID link를 유지하는지 필요한 최소 회귀를 추가한다.
- 관련 Node tests, 변경 파일 ESLint, frontend build를 실행한다.

## 금지

- backend Python 코드와 API response shape 변경 금지.
- private profile/mapping/DSN 파일 생성·수정·읽기 금지.
- 실제 PostgreSQL/CNU 데이터/Ollama/network/browser 호출 금지.
- legacy 시스템, SQLite upload/review/publish 흐름 변경 금지.
- 계획/ADR/handoff/사람 체크포인트 수정, 전체 Python pytest, compileall, commit, push 금지. 이는 메인 Codex가 담당한다.

## 완료 보고

긴 diff와 전체 로그를 출력하지 않는다. 한국어로 변경 파일, 핵심 계약, 관련 테스트 결과, 실패 원인, 남은 위험만 간결히 보고한다.
