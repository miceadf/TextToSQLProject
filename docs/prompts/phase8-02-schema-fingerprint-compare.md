# Phase 8-02 구현 지침: schema fingerprint 비교와 활성 mapping 영향 분석(읽기 전용)

## 배경

private SQLite 등록은 불변이므로(Phase 6-17) 같은 논리 DB의 schema가 바뀌면 관리자는 새 파일을 **새 database ID로 등록**한다. 이때 기존에 게시·활성화된 mapping을 새 등록에 그대로 쓸 수 있는지, 어떤 field가 영향을 받는지 알아야 8-03(`STALE` 처리·영향 field만 재검토)을 설계할 수 있다. 8-02는 두 등록의 schema를 비교하고 활성 mapping의 영향을 계산하는 **읽기 전용** 단계다. 어떤 파일도 쓰지 않고 실행 권한도 바꾸지 않는다.

## 범위

### 1. 순수 비교 (`backend/adaptive/catalog/compare.py`, 신규)

- `compare_catalogs(base, candidate) -> CatalogDiff`(`catalog-diff-v1`). dialect 무관 순수 함수로, PostgreSQL catalog에도 쓸 수 있다.
  - fingerprint 두 개와 `identical`(fingerprint 동일).
  - 추가·삭제된 table.
  - 공통 table의 column 변화: `ADDED`·`REMOVED`·`TYPE_CHANGED`(declared type 또는 affinity)·`NULLABILITY_CHANGED`·`PRIMARY_KEY_CHANGED`.
  - FK 변화: `ADDED`·`REMOVED`(table·column·참조 table·참조 column 기준).
  - 이름은 정확히 일치할 때만 같은 것으로 본다. 이름 변경 추정은 하지 않는다(삭제 + 추가로 보고).
  - `sample_values`·`default_sql`·hidden/generated 원문은 비교하거나 반환하지 않는다(값 노출 방지).
  - 변경 목록은 합계 500개까지만 반환하고 `truncated`로 표시한다. 순서는 결정적(table, column, 변경 종류)이다.
- `mapping_impact(profile, candidate) -> list[FieldImpact]`: 활성 mapping의 field마다 `UNCHANGED`·`TYPE_CHANGED`·`COLUMN_MISSING`·`TABLE_MISSING`. base와 candidate의 column 속성(declared type·affinity·nullable)을 비교한다.

### 2. 등록 간 비교 (`backend/adaptive/registration/reviews.py`)

- `compare_registrations(repository_root, base_database_id, candidate_database_id) -> RegistrationComparison`:
  - base는 strict `load_activation`이 성공하는 게시·활성 등록이어야 한다(실행 중 mapping 기준).
  - candidate는 strict `load_registration`이 성공하는 등록(검토 대기 포함)이다. base와 같은 ID는 거부.
  - diff, field 영향, `carry_over_executable`(base mapping을 candidate ID·fingerprint로 옮겼을 때 canonical 전체 coverage·catalog 실행 가능성 검사를 통과하는지; 모든 field가 존재하고 ontology version이 같을 때만 계산, 아니면 false), 두 ontology version 일치 여부.
  - 등록·검토·activation artifact 쓰기·lock 없음(strict reload의 기존 임시 검증 사본은 예외로 생성 뒤 삭제).

### 3. HTTP (`GET /api/adaptive/v1/registrations/compare?base_database_id=..&candidate_database_id=..`)

- 두 query parameter만 정확히 허용(중복·추가 key 거부). 응답은 `registration-compare-v1` metadata만: fingerprint, 요약 수, 변경 목록, field 영향, `carry_over_executable`, `ontology_version_match`, base 활성 mapping 버전. physical table·column 이름은 기존 관리자 검토 화면과 같은 범위로 포함하되 경로·행·sample·SQL·traceback은 포함하지 않는다.
- 고정 오류: `SCHEMA_COMPARE_INVALID_REQUEST`(400, 형식 오류·같은 ID), `SCHEMA_COMPARE_NOT_FOUND`(404, base 미활성·등록 없음), `SCHEMA_COMPARE_INTERNAL_ERROR`(500).

### 4. frontend

- `src/adaptive-prototype/schemaCompare.ts`: strict parser와 client.
- `SchemaComparePanel.tsx`: 업로드 직후 검토 대기 DB 아래에서, registry의 private 활성 DB 중 하나를 기준으로 골라 비교 결과(동일 여부, table·column·FK 변화, field 영향, 그대로 옮겨 실행 가능 여부)를 읽기 전용으로 보여준다. 기준 후보는 MainPage가 이미 가진 registry에서 `schema === 'private'`이고 자기 자신이 아닌 항목만 전달한다.
- legacy 화면 동작은 바꾸지 않는다(MainPage에는 기존 업로드 결과 블록 안에 패널 한 개만 추가).

## 제외

- mapping 승계·`STALE` 표시·후속 등록 연결 기록(8-03).
- PostgreSQL profile 재검사 연결(순수 함수만 공유).
- 이름 변경 추정, 코드값 비교.

## 보안 계약(유지)

등록·mapping·activation 파일 불변, 비교는 실행 권한을 바꾸지 않음, 미게시 candidate의 SQL 실행 없음(`carry_over_executable`은 compile·AST policy 검사까지만 하고 실행하지 않는다), sample·행·경로 비노출, 고정 오류 code.

## 테스트

- 순수 비교: 동일 catalog, table 추가·삭제, column 추가·삭제·타입·nullable·PK 변화, FK 추가·삭제, 결정적 순서, 500개 상한과 `truncated`, sample·default 값 비노출.
- field 영향: 각 상태, flat→normalized 같은 구조 변경에서 `TABLE_MISSING`.
- 등록 비교: 동일 schema의 새 등록은 `identical`·전 field `UNCHANGED`·`carry_over_executable` true; column 이름 변경·삭제는 해당 field `COLUMN_MISSING`·false; FK 삭제로 JOIN 불가한 normalized는 field는 `UNCHANGED`여도 false; base 미게시·같은 ID·없는 ID는 거부; 파일 변경 없음.
- HTTP: query strictness, 응답 shape, 경로·sample 비노출.
- frontend: parser 거부 사례와 client 요청 URL.
