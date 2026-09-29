# Phase 8-03 구현 지침: 영향 field `STALE` 표시와 영향 없는 field 승계로 검토 시작

## 배경

8-02는 새 등록과 기준(게시·활성) 등록의 schema 차이와 활성 mapping 영향을 읽기 전용으로 보여준다. 8-03은 그 결과로 **새 등록의 검토를 시작**하는 명시적 관리자 행위를 추가한다. 영향이 없는 field는 기준 mapping을 승계하고, 영향받은 field만 `STALE`로 표시해 다시 검토하게 한다. 자동 publish·실행 권한 변경은 없다.

로드맵 "영향받은 mapping을 `STALE` 처리"는 등록 불변 모델에서 다음 뜻으로 구현한다: 기준 등록의 파일·mapping은 그대로 유효하고(같은 파일에서는 계속 실행 가능), schema가 바뀐 새 등록에서 기준 mapping의 해당 field가 **그대로 쓸 수 없는 상태(STALE)** 로 기록된다.

## 범위

### 1. storage (`backend/adaptive/registration/reviews.py`)

- `carry_over_review(repository_root, database_id, base_database_id, reviewer) -> MappingDraft`:
  - `database_id`(대상)와 `base_database_id`는 달라야 한다. 기준은 strict activation load가 되어야 한다(8-02 `compare_registrations` 재사용).
  - ontology version이 다르면 `PUBLISH_BLOCKED`(승계 불가).
  - 대상은 한 번도 publish되지 않아야 한다(`current.json` 없음). 첫 검토에만 쓴다.
  - 대상 draft가 없으면 proposal에서 새 draft를 만든다. 이미 있으면 모든 field가 `PENDING`일 때만(검토 행위 전) 대체한다. 그 외는 `REVIEW_CONFLICT`.
  - 영향이 `UNCHANGED`인 field만 기준 활성 mapping의 table·column으로 `REMAP`하고 사유를 `carried over from <base> mapping-vN; schema unchanged`로 남긴다. reviewer는 요청자다.
  - `TYPE_CHANGED`·`COLUMN_MISSING`·`TABLE_MISSING` field는 `PENDING`으로 두고 `STALE`로 기록한다.
  - 승계 기록 `succession.json`(`mapping-succession-v1`): 대상 ID, 기준 ID·활성 mapping 버전·mapping SHA-256·schema fingerprint, 대상 schema fingerprint, `carried_fields`, `stale_fields`(field·영향 상태), `initiated_by`, `initiated_at`. 새 파일로만 쓰고 덮어쓰지 않는다.
  - 순서: draft 교체 → 승계 기록 생성. 중간에 끊긴 경우 같은 요청 재시도는 draft가 이 승계 결과와 정확히 같으면 기록만 마저 쓴다(idempotent). 승계 기록이 이미 있으면 같은 기준·버전·field 분류일 때만 그대로 반환하고, 다르면 `REVIEW_CONFLICT`.
  - review 디렉터리 파일 허용 목록에 `succession.json`을 추가한다. 이 파일은 실행 권한과 무관하며 `load_activation`은 내용을 쓰지 않는다.
- `load_succession(repository_root, database_id) -> MappingSuccession | None`: strict load, 대상 ID·fingerprint가 현재 등록과 맞지 않거나 손상되면 None(표시만 생략).
- publish는 바뀌지 않는다: 모든 field 검토 완료, 실행 가능성 검사를 그대로 요구한다. 승계 field도 관리자가 다시 remap·approve할 수 있다.

### 2. HTTP

- `POST` action `CARRY_OVER`: `{"action","database_id","base_database_id","reviewer"}` strict body, 성공 시 기존 detail 응답.
- detail(GET·POST 응답)에 승계 기록이 있으면 `succession`: `{base_database_id, base_mapping_version, carried_fields, stale_fields: [{semantic_field, status}]}`. 없으면 key 없음. 기존 detail shape는 그대로다.
- 고정 오류 매핑: 같은 ID·형식 오류 400, 기준 미활성·대상 없음 404, 이미 검토 중·게시됨·기록 불일치 409, ontology 불일치 422(`PUBLISH_BLOCKED`, fields 없음).

### 3. frontend

- `mappingReview.ts`: detail parser가 선택 `succession`을 strict 검증(ID 형식, 버전, carried·stale 서로소, stale 상태 값, 모든 이름이 detail field에 존재), `carryOverReview` client.
- `MappingReviewPanel.tsx`: 검토 시작 전 화면에 "기준 DB mapping 승계로 시작"(기준 선택 + 검토자 필수). 검토 화면에서 stale field에 `STALE · 재검토 필요(사유)`, 승계 field에 `승계됨` 표시, 상단에 승계 요약.
- `MainPage.tsx`: 패널에 registry의 private 활성 DB 목록(자기 자신 제외)을 전달하는 prop 하나만 추가.

## 제외

- 기준 등록을 superseded로 표시하거나 registry에서 자동 교체하는 일(명시적 publish 뒤 관리자가 선택).
- PostgreSQL live schema drift의 `STALE` 연결(현재는 `is_postgres_context_current`로 fail closed).
- 이름 변경 추정 승계, 코드값 비교.

## 보안 계약(유지)

미게시 draft 실행 금지, 승계는 draft만 바꾸고 실행 권한을 만들지 않음, 명시적 publish만 실행 권한 부여, 등록·mapping·activation 불변, sample·행·경로·SQL 비노출, 고정 오류 code.

## 테스트

- 같은 schema: 전 field 승계, stale 없음, 바로 publish 가능하고 publish 결과 mapping이 기준과 같은 column.
- column 이름 변경: 해당 field만 stale·PENDING, publish는 `PUBLISH_BLOCKED`(field 목록에 stale field), remap 뒤 publish 성공.
- 구조 변경(flat→hybrid): 전 field stale.
- 전제 위반: 같은 ID, 기준 미게시, 대상 게시됨, 검토 행위가 있는 draft, 다른 기준으로 재승계는 거부; 같은 요청 재시도 idempotent; draft만 쓰이고 기록이 없는 중단 상태 복구.
- 손상된 승계 기록은 detail에서 생략되고 activation·registry에 영향 없음.
- HTTP strict body, detail `succession` shape, 경로 비노출.
- frontend parser 거부 사례와 client body.
