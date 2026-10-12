# Phase 9-11 지침: 게시된 PostgreSQL DB의 "mapping 검토 시작" 오류 고침

## 증상 (사용자 보고, Claude 재현)

- 게시된 PostgreSQL 등록(`rehearsal-school`, mapping v2)에서 관리자 화면의 "mapping 검토 시작"을 누르면 "응답 형식이 올바르지 않습니다. 다시 시도해 주세요."가 나온다.
- 재현: 사용자 서버의 읽기 전용 `GET /api/adaptive/v1/registrations/review?database_id=rehearsal-school` 응답을 `src/adaptive-prototype/mappingReview.ts`의 `parseReviewDetail`에 넣으면 `null`이다. INIT 응답도 같은 형식이라 같은 이유로 실패한다.

## 원인 (확인됨)

`backend/adaptive/api/postgres_registration.py`의 review 상세(약 222~229행)가 만드는 `mapping_versions` 항목이 화면 계약과 다르다. 두 가지를 모두 고쳐야 통과한다(하나만 고치면 여전히 `null`).

1. `published_at`이 마이크로초를 포함한다(`2026-10-12T12:39:49.389950Z`). 화면은 `UTC_TIMESTAMP_PATTERN = /^...T\d\d:\d\d:\d\dZ$/`(초까지)만 받는다. 저장 형식은 `registration/postgres_registrations.py` 약 927행 `isoformat()`이다.
2. 현재 버전에 `activatable: False`를 보낸다. 화면은 `active && !activatable`을 잘못된 이력으로 본다(`parseVersionHistory`). SQLite 경로(`registration/reviews.py`)는 활성 버전을 `activatable: True`로 보낸다.

## 고칠 것

- 서버 응답을 기존 화면 계약에 맞춘다. 화면 parser는 바꾸지 않는다.
  - review 상세의 `published_at`은 초 단위 UTC `YYYY-MM-DDTHH:MM:SSZ`로 보낸다. 이미 저장된 `published.json`(마이크로초 포함)도 그대로 읽혀야 하므로 응답을 만들 때 자른다. 저장 형식은 바꾸지 않는다(기존 파일과 다른 읽는 곳 보존).
  - 현재 버전 항목은 `activatable: True`로 보낸다. PostgreSQL 경로에 이전 버전 재활성화(ACTIVATE)가 없다면 그 동작은 그대로 두고, 화면에 재활성화 버튼이 새로 생기지 않는지 확인한다(활성 버전에는 버튼이 없어야 한다).
- 다른 응답(게시 결과, 등록, 챗봇)은 바꾸지 않는다.

## 테스트

- 서버: 게시된 PostgreSQL 등록의 review GET과 INIT 응답에서 `published_at`이 초 단위 `Z` 형식이고 활성 버전이 `activatable: True`인지(마이크로초가 있는 저장 파일로).
- 계약: 그 서버 응답 모양을 `parseReviewDetail`이 받아들이는지(기존 frontend 테스트 방식대로 `tests/frontend/`에 fixture 하나).
- 관련 기존 테스트 파일만 실행한다. 전체 pytest는 돌리지 않는다.

## 지킬 것

- 사용자 서버(8000·8123·5173)와 사용자 데이터(`.schema-adaptive/postgres-registrations/rehearsal-school` 등)를 건드리지 않는다. 확인이 필요하면 테스트용 임시 디렉터리만 쓴다.
- live 모델 호출 없음, 커밋 없음, `backend/.env` 값 출력 없음.
- 문서: `docs/schema-adaptive-text-to-sql-plan.md` 변경 이력에 2026-10-12 Phase 9-11 한 줄(증상, 원인 두 가지, 서버 쪽 고침), 로드맵에 9-11 항목.
- 끝나면 바꾼 파일과 테스트 결과를 짧게 적는다.
