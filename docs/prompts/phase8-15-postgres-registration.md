# Phase 8-15 지침: 화면에서 PostgreSQL DB 등록(서버 연결 목록 방식)

작성일: 2026-09-30.

사용자 결정:
- 접속 정보는 B, 즉 서버 연결 목록으로 받는다. 비밀번호는 브라우저를 거치지 않는다.
- 8-15와 8-07의 순서는 개발자 판단에 맡긴다. 8-15를 먼저 한다. 그래야 8-07 검토 화면을 SQLite와 PostgreSQL 공통으로 한 번에 만들 수 있다.

## 1. 목표

"DB 변경" 화면에서 PostgreSQL 연결과 schema를 골라 등록한다. 등록한 DB는 SQLite와 같은 검토 화면에서 검토한 뒤 게시하며, 게시하면 서버를 재시작하지 않아도 DB 목록·챗봇(QueryIR, 값 힌트, SQL 표시)에 바로 붙는다.

## 2. 연결 목록(서버 소유)

- 연결 목록은 `backend/.env`에서 만든다.
  - `DATABASE_URL`은 연결 `default`("기본 PostgreSQL 서버")가 된다.
  - `PG_SOURCE_<NAME>=<DSN>`은 연결 `<name>`(소문자, `_`→`-`)이 된다.
- 브라우저에는 연결 ID와 이름만 보낸다. DSN·계정·비밀번호는 보내지 않는다.
- schema 목록은 읽기 전용으로 조회한다. `pg_catalog`, `information_schema`, `pg_toast`, `pg_temp*`는 뺀다.

## 3. 등록

- 입력: 연결 ID, schema, database ID, 이름. view 포함 여부는 이번에 다루지 않는다(table만).
- 순서:
  1. 읽기 전용으로 구조를 확인한다(기존 `PostgresAdapter`). table이 없으면 거부한다.
  2. 규칙 제안을 만들고, 모델이 설정돼 있으면 8-12 AI column 제안을 만든다.
     - AI 제안에는 저장 값 예시가 필요하다. column마다 서로 다른 값을 최대 10개 읽어(8-13 reader) 메모리에서만 쓴다.
     - column이 300개를 넘으면 예시 없이 규칙만 쓴다.
  3. private 저장소 `.schema-adaptive/postgres-registrations/<id>/`에 `registration.json`, `catalog.json`, `proposal.json`을 쓴다.
     - 등록 기록은 immutable이고, strict reload한다.
     - Git이 추적하지 않는 경로여야 한다.
     - 저장 파일에는 예시 값을 넣지 않는다.
- database ID는 다음 어느 것과도 겹치면 거부한다: SQLite 등록, 기존 PostgreSQL profile, 고정 합성 ID.
- 응답은 SQLite 업로드 응답(v2)과 같은 모양이다. 버전은 `private-postgres-registration-v1`이다.

## 4. 검토와 게시

- 검토 API 경로(`/registrations/review`)는 같다. database ID가 PostgreSQL 등록이면 서버가 PostgreSQL 처리로 넘긴다. 응답 모양도 같아서 화면을 그대로 쓴다.
- 동작: `INIT`, `APPROVE`, `REMAP`, `IGNORE`(새로 추가), `PUBLISH`.
  - `IGNORE`는 "이 DB에 이 정보가 없음"이며 사유가 필요하다. SQLite에서도 draft에는 기록되지만, SQLite 게시는 지금처럼 15개 field가 모두 있어야 한다.
  - `ACTIVATE`·`CARRY_OVER`·test query·회귀 비교는 PostgreSQL 등록에서 아직 지원하지 않는다(고정 오류 code).
- 게시 조건:
  - 모든 field가 승인·재지정·무시 중 하나여야 한다. 대기 상태가 남으면 거부한다.
  - 연결된 field가 하나 이상 있어야 한다.
- 게시 때 서버가 정하는 것(모델 없음, 결정적 규칙):
  - **변환:** field type과 column type이 맞고 저장 값 예시(최대 50개)가 형식에 맞는 변환이 정확히 하나일 때만 붙인다. 예: `time` column → `TIME_TO_HHMM`, `3/3/0` 형식 → `SLASH_FIRST_NUMERIC`, 한 글자 요일 → `KOREAN_WEEKDAY`.
  - **기준 table:** `CourseOffering.section`의 table이다(8-09 규칙). 분반이 없으면 기준 table도 없다.
  - **미지원 field:** 무시한 field다.
  - **값 사전:** 없음(8-07에서 코드표·약어 입력).
- 검증:
  - 실제 연결로 기존 runtime 검증(`build_validated_contexts`)을 통과해야 한다. fingerprint, 형식, 기준 table 경로 유일성을 본다.
  - 실패하면 `PUBLISH_BLOCKED`다.
- 저장:
  - `.schema-adaptive/postgres-profiles/<id>/`에 profile v4와 mapping 쌍을 쓴다. 기존 startup loader가 읽는 형식이며, DSN 환경변수 이름은 `SCHEMA_ADAPTIVE_POSTGRES_DSN_REG_<ID>`다.
  - 등록 폴더에 게시 기록 `published.json`(버전·게시자·시각)을 쓴다.
  - 다시 게시하면 버전이 올라가고 profile을 교체한다.

## 5. 실행 중 추가

- 게시에 성공하면 검증된 context를 실행 중인 서버의 PostgreSQL context 집합에 추가한다. 추가만 하고, 같은 ID가 있으면 교체한다.
- DB 목록·강의 목록·챗봇·값 힌트 reader가 같은 집합을 본다.
- 서버를 재시작하면 dev launcher가 게시된 등록을 찾아 연결 목록의 DSN을 해당 환경변수로 넣고 profile을 읽는다.

## 6. 화면

- "DB 변경" 창에 "PostgreSQL 등록" 영역을 추가한다: 연결 선택 → schema 선택 → ID·이름 → 등록.
- 결과 요약과 검토 패널은 SQLite 업로드와 같다.
- 검토 패널에 "이 DB에 없음(IGNORE)" 버튼과 사유 입력을 추가한다.

## 7. 테스트

- 단위(fake connector·adapter):
  - 연결 목록 파싱, DSN 비노출, schema 목록.
  - 등록 ID 충돌, strict reload, 예시 값 비저장.
  - IGNORE, 게시 조건, 변환·기준 table 결정, 실패 시 파일 없음.
  - 실행 중 추가 뒤 DB 목록·챗봇 연결.
- frontend parser와 client.
- live:
  - 합성 DB(`schema_adaptive_synthetic`)에 임시 schema를 만든다.
  - 화면 API로 등록 → 검토 → 게시 → 챗봇 질문까지 확인한다.
  - 끝나면 schema와 private 파일을 지운다.

## 8. 제약

- 읽기 전용이며 legacy DB는 수정하지 않는다.
- DSN은 브라우저·stdout에 내보내지 않는다.
- 게시된 mapping만 실행하고, 모델 호출 수·검증 경로는 변경하지 않는다.
- 사람 관문을 표시하지 않고, push하지 않는다.
