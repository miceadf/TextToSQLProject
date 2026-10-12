# Phase 9-13 지침: PostgreSQL 등록 DB에서도 시험 질의·기준 저장·회귀 확인이 되게 하기

## 배경 (2026-10-13 사용자 결정: B안)

- 관리자 화면 "검토·게시"의 **시험 질의**(test query 미리보기·실행), **기준 결과 저장**(verified queries), **회귀 확인**(regression)은 SQLite 등록 저장소만 읽는다(`backend/adaptive/api/admin_test_queries.py`: `run_test_query` → `review_storage.load_activation`, `verify_test_query`, `run_regression` → `review_storage.load_published_version`).
- PostgreSQL 등록(`rehearsal-school`, mapping-v3 게시됨)에서 시험 질의를 누르면 "게시·활성화된 DB에서만 test query를 실행할 수 있습니다", 그리고 "회귀 확인 실패: ..."가 나온다(사용자 스크린샷). 게시는 정상이다.
- 사용자 결정: 숨기지 말고 PostgreSQL에서도 동작하게 한다.

## 만들 것

1. **시험 질의 실행 (PostgreSQL)**
   - `TEST_QUERIES`(offerings, courses, departments, schedules, seats)를 PostgreSQL 등록의 **게시된 현재 버전**으로 실행한다.
   - 챗봇과 같은 PostgreSQL 실행 경로를 재사용한다: 게시된 runtime profile(`.schema-adaptive/postgres-profiles/<id>/`)과 `api/postgres_runtime.py`의 검증된 context(`ValidatedPostgresContext`, 변환·값 사전·anchor·"이 DB에 없음" 항목 포함), PostgreSQL compiler·`SchemaAdaptivePostgresPolicy`·`PostgresReadOnlyExecutor`. 새 SQL 생성 경로를 만들지 않는다. 모델 호출 없음(고정 QueryIR).
   - "이 DB에 없음"인 항목이 select에 있으면, 챗봇의 규칙과 같게 그 항목을 빼고 실행하고 결과에 "빠진 항목" 목록을 알린다. 남는 항목이 없으면 고정 코드로 "이 DB에서는 실행할 수 없음"을 돌려준다(오류 500이 아니라 설명 가능한 결과).
   - 응답 모양은 SQLite 경로와 같게 한다(화면 parser 그대로 통과). 필요한 필드가 하나 더 있으면(빠진 항목) 화면 parser와 표시를 함께 넓힌다.
   - 행 미리보기·상한은 기존 `MAX_PREVIEW_ROWS`·`MAX_RESULT_ROWS` 그대로.
2. **기준 결과 저장과 회귀 확인 (PostgreSQL)**
   - 기준 저장: 현재 버전의 시험 질의 결과를 서버가 계산해 기준으로 저장한다. 저장 위치는 그 등록 디렉터리(`.schema-adaptive/postgres-registrations/<id>/` 아래 새 파일). 형식과 검증은 SQLite 기준 저장과 같은 규칙(검토자, 결과 fingerprint, 원본 행 값을 남기지 않는 기존 원칙이 있으면 그대로).
   - 회귀 확인: PostgreSQL은 이전 버전 파일을 따로 보관하지 않으므로 **현재 버전**에 대해서만 기준과 비교한다. 다른 버전 번호를 요청하면 고정 코드로 거절한다.
   - 이에 맞춰 review 상세의 PostgreSQL 활성 버전 `activatable`을 `True`로 되돌린다(9-11b에서 False로 둔 이유였던 "회귀 확인 오류"가 사라지므로). 활성 버전에는 "되돌리기" 버튼이 원래 안 보이므로 재활성화 기능은 새로 생기지 않는다. 9-11b의 화면 계약 완화(활성·`activatable: false` 허용)는 그대로 둔다.
3. **라우팅**: 같은 HTTP 경로(`/registrations/test-queries`, `/test-query`, `/verified-queries`, `/regression`)에서 `database_id`가 PostgreSQL 등록이면 PostgreSQL 경로로, 아니면 기존 SQLite 경로로 보낸다(`PostgresRegistrationService.handles` 같은 기존 판별 재사용). SQLite 동작은 바꾸지 않는다.
4. **화면**: 서버 응답이 같으면 바꿀 것이 없다. 빠진 항목 표시가 필요하면 시험 질의 결과 위에 "이 DB에 없어 뺀 항목: ..." 한 줄을 보인다. 자동으로 회귀 확인을 부르는 곳이 있으면 PostgreSQL에서도 정상 동작하는지 확인한다.

## 테스트

- 가짜 PostgreSQL executor·connector와 임시 디렉터리의 게시된 profile로:
  - 시험 질의 5종이 게시 mapping으로 컴파일·실행되고 SQLite와 같은 응답 모양.
  - "이 DB에 없음" 항목이 빠지고 알려짐; 모두 빠지면 고정 코드.
  - 기준 저장 → 회귀 확인 일치/불일치; 다른 버전 번호 거절.
  - review 상세 활성 버전 `activatable: True`, `published_at` 초 단위 유지.
  - SQLite 경로 기존 테스트 그대로 통과.
- frontend: 바뀐 parser·표시가 있으면 해당 테스트.
- 관련 테스트 파일만 실행한다. 전체 pytest는 돌리지 않는다.

## 지킬 것

- 사용자 서버(8000·8123·5173)와 사용자 데이터(`.schema-adaptive/postgres-registrations/rehearsal-school` 등)를 건드리지 않는다. 테스트는 임시 디렉터리만.
- legacy DB 읽기 전용, live 모델 호출 없음, 커밋 없음, `backend/.env` 값 출력 없음, DSN 노출 없음.
- 문서: `docs/schema-adaptive-text-to-sql-plan.md` 로드맵 9-13, 변경 이력 2026-10-13 한 줄(사용자 결정 B, 현재 버전만 회귀 확인).
- 끝나면 바꾼 파일과 테스트 결과를 짧게 적는다.
