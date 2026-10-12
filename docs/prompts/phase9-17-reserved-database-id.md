# Phase 9-17 지침: 예약된 DB id(`legacy`) 등록 막기와 DB 목록 부분 실패 처리

## 증상 (사용자 보고, Claude 재현)

- 사용자가 관리자 화면 "DB 등록"에서 레거시 public schema를 Database ID `legacy`로 등록·게시했다. 그 뒤 메인 화면과 "DB 변경" 창이 "DB 목록을 불러오지 못했습니다. legacy 기준선으로 동작합니다."를 보이고 rehearsal 등 모든 DB가 사라졌다.
- 서버 `GET /api/adaptive/v1/databases`는 200으로 6개(`synth-clarification-prototype`, `synth-clarification-legacy`, `legacy-base`, `legacy-view`, `rehearsal-school`, `legacy`)를 정상으로 준다.
- 원인: frontend `src/adaptive-prototype/databases.ts`의 `LEGACY_SELECTION_ID = 'legacy'`(Legacy 기준선 선택지의 내부 id)와 등록 id `legacy`가 겹쳐 `parseDatabaseRegistry`가 목록 전체를 `null`로 돌린다. 그 응답에서 `legacy` 항목만 빼면 parser가 통과한다(재현).

## 고칠 것

1. **등록에서 예약 id 거절 (서버)**: PostgreSQL 등록(`api/postgres_registration.py`/`registration/postgres_registrations.py`)과 SQLite 업로드 등록의 database_id 검증에 예약 id 목록을 더한다. 최소 `legacy`. Legacy 기준선·화면이 예약해 쓰는 다른 id가 있으면 함께(코드에서 찾아 목록으로). 거절은 기존 INVALID_INPUT 계열 고정 코드 + 화면에서 "이 ID는 시스템이 쓰는 이름이라 쓸 수 없습니다" 같은 안내.
2. **화면 등록 양식**: 같은 예약 id를 제출 전에 막고 안내한다.
3. **DB 목록 부분 실패**: `parseDatabaseRegistry`가 예약 id와 겹치거나 형식이 잘못된 항목 **하나 때문에 전체를 버리지 않게** 한다. 그 항목만 빼고 나머지를 쓰며, 뺀 항목이 있으면 "일부 DB(`legacy`)는 이름이 예약어와 겹쳐 목록에서 뺐습니다" 같은 안내를 한 줄 보인다. 응답 전체 형식이 틀린 경우(최상위 키·version 등)는 지금처럼 실패로 둔다. 중복 id 등 다른 엄격 검사는 그대로.
4. 이미 저장된 `legacy` 등록 데이터는 건드리지 않는다(사용자가 지울지 결정).

## 테스트

- 서버: id `legacy`로 등록 시도 → 고정 코드 거절, 파일이 생기지 않음. 다른 id는 그대로 성공.
- frontend: 예약 id 항목이 섞인 목록에서 나머지가 파싱되고 경고가 나옴; 최상위 형식 오류는 여전히 null; 등록 양식이 `legacy`를 막음.
- 관련 테스트 파일만 실행, `npx tsc -b`.

## 지킬 것

- 사용자 서버·데이터(`.schema-adaptive/postgres-registrations/legacy` 포함) 안 건드림, live 모델 호출 없음, 커밋 없음, `backend/.env` 값 출력 없음. UTF-8로 쓴다.
- 문서: `docs/schema-adaptive-text-to-sql-plan.md` 로드맵 9-17, 변경 이력 2026-10-13 한 줄.
- 끝나면 바꾼 파일과 테스트 결과를 짧게 적는다.
