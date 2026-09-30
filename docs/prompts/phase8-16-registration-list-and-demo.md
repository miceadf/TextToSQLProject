# Phase 8-16 지침: 등록 목록(다시 열기), 처음부터 끝까지 browser smoke, 시연 절차

작성일: 2026-09-30. 사용자 결정: 진행. 순서는 ADR-078의 프로토타입 마지막 단계를 따른다.

## 1. 등록 목록

- 문제: 페이지를 새로고침하면 검토 중이던 PostgreSQL 등록으로 돌아갈 길이 없다.
- API `GET /api/adaptive/v1/postgres/registrations`는 화면 등록 PostgreSQL DB 목록을 돌려준다.
  - 항목: `database_id`, `label`, `source_id`, `schema`, `status`(`PENDING_REVIEW`/`PUBLISHED`), `mapping_version`(게시 전 `null`).
  - DSN과 물리 이름은 넣지 않는다. schema 이름은 관리자가 고른 값이라 보여 준다.
  - 손상된 등록은 목록에서 빼고, 요청 전체를 실패시키지 않는다.
- 화면: PostgreSQL 등록 영역에 "등록된 PostgreSQL DB" 목록을 두고, 항목마다 "열기" 버튼으로 검토·값 사전 패널을 다시 연다.

## 2. 처음부터 끝까지 browser smoke

- 새 코드를 확인용 포트(adaptive 8124, 화면 5174)에 띄운다. 사용자 서버(8000/8123/5173)는 건드리지 않는다.
- 시나리오: 레거시 DB를 화면에서 등록한다(읽기 전용).
  1. DB 변경 → PostgreSQL 등록(연결 `default`, schema `public`).
  2. 검토: 후보를 보고 승인하거나 "이 DB에 없음"을 누른다(이름 함정 확인).
  3. 값 사전: 학과 약어 `컴융`.
  4. 게시.
  5. 새로고침한 뒤 목록에서 다시 열리는지 확인한다.
  6. DB 선택 → 강의 목록·챗봇 "컴융 전공 과목" → 이해한 조건과 SQL을 확인한다.
- 끝나면 확인용 등록·profile 파일을 지우고 확인용 서버를 끈다.
- 결과와 발견한 문제를 결과 문서에 적는다. 막는 문제는 고친다.

## 3. 시연 절차 문서

`docs/demo-procedure.md`에 둔다.
- 준비: `backend/.env`, Ollama 모델 2개, 서버 실행과 상태 줄 읽는 법.
- 시연 순서: 위 시나리오와 말할 요점.
- 되돌리기: 등록 파일 삭제 위치.
- 주의: SQL 표시(`SHOW_SQL=0`), 실제 학교 자료 사용 전 E3/L5, 외부 시연 전 L5.

## 4. 제약

- 읽기 전용이며 legacy DB는 수정하지 않는다.
- DSN은 브라우저에 보내지 않는다.
- 사람 관문을 표시하지 않고, push하지 않는다.
