# Phase 9-18 지침: 등록된 PostgreSQL DB 삭제 기능

## 배경 (2026-10-13 사용자 요청)

- 관리자 화면 "DB 등록" 탭의 "등록된 PostgreSQL DB" 표에서 등록을 지울 방법이 없다. 지금은 `.schema-adaptive/postgres-registrations/<id>`와 `postgres-profiles/<id>` 폴더를 손으로 지우고 서버를 다시 켜야 한다.
- 당장의 사례: 예약 id와 겹친 `legacy` 등록(9-17)을 지우고 다른 id로 다시 등록하려 한다.

## 만들 것

1. **서버 API** (`api/postgres_registration.py`): `DELETE /api/adaptive/v1/postgres/registrations?database_id=<id>` (또는 기존 경로 규칙에 맞는 POST action). 본문/파라미터 엄격 검증.
   - 그 id의 PostgreSQL 등록 폴더(`postgres-registrations/<id>`: registration·catalog·proposal·draft·published·dictionaries·extensions·verified-queries 등)와 게시 profile 폴더(`postgres-profiles/<id>`)를 지운다.
   - **안전**: 경로는 기존 `pg._private_dir` 같은 검증 함수로만 만든다(경로 탈출·symlink 거절, 폴더가 symlink면 지우지 않고 거절). `postgres-registrations`에 없는 id(예: `legacy-view`, `legacy-base` profile, 합성 DB, SQLite 등록)는 고정 코드 NOT_FOUND로 거절한다. 예약 id(`legacy`)라도 이미 저장된 PostgreSQL 등록이면 지울 수 있어야 한다(9-17 정리용).
   - 실행 중 서버 상태에서도 빠지게: live chat context(`LivePostgresContexts`), 값 reader 등록, DB 목록(`/api/adaptive/v1/databases`), 등록 목록에서 즉시 사라진다(재시작 불필요). 등록 lock 안에서 처리.
   - 평가 기록(`.schema-adaptive/blind-evals/...`, `direct-sql-pg/...`)은 지우지 않는다.
   - 응답: `{database_id, deleted: true}` 같은 작은 고정 형식. 오류는 고정 코드만(경로·DSN 노출 없음).
2. **화면** (`PostgresRegistrationPanel.tsx`): 표의 각 줄 "동작"에 [삭제] 버튼.
   - 누르면 확인 상자: "`<id>`를 삭제합니다. 등록 정보, 검토·게시 내용, 값 사전, 확장 항목, 시험 질의 기준이 모두 지워지고 되돌릴 수 없습니다. 확인하려면 ID를 그대로 입력하세요." ID 입력이 정확히 같을 때만 [삭제] 활성.
   - 성공하면 표·DB 목록·"사용할 DB"를 새로 고친다. 지금 선택된 DB를 지웠으면 Legacy 기준선으로 돌아간다(기존 "DB 목록 실패 시 legacy" 동작과 같은 방식).
   - 검토·게시 탭에 그 DB가 열려 있었으면 닫는다.

## 테스트

- 서버(임시 디렉터리, 가짜 connector): 게시된 등록 삭제 → 두 폴더 없음, live context·DB 목록·등록 목록에서 빠짐, 같은 id로 재등록 가능(예약 id 제외). 없는 id·profile 전용 id·합성 id·경로 탈출·symlink 거절. 평가 기록 폴더는 남음.
- frontend: 확인 ID가 다르면 삭제 비활성, 성공 후 목록 갱신, 선택 중 DB 삭제 시 legacy 기준선으로.
- 관련 테스트 파일만, `npx tsc -b`.

## 지킬 것

- 사용자 서버·데이터(특히 `.schema-adaptive/postgres-registrations/legacy`, `rehearsal-school`)를 건드리지 않는다. 지우는 시험은 임시 디렉터리에서만.
- live 모델 호출 없음, 커밋 없음, `backend/.env` 값 출력 없음. UTF-8로 쓴다.
- 문서: `docs/schema-adaptive-text-to-sql-plan.md` 로드맵 9-18, 변경 이력 2026-10-13 한 줄(사용자 요청, PostgreSQL 등록만, 평가 기록은 남김).
- 끝나면 바꾼 파일과 테스트 결과를 짧게 적는다.
