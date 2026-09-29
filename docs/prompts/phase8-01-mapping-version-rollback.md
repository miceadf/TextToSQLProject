# Phase 8-01 구현 지침: mapping 버전 이력과 이전 버전 재활성화(rollback)

## Phase 8 분할 계획

Phase 6-17~6-19에서 합성 SQLite의 private 등록·loopback upload·`PENDING_REVIEW` 요약·필드 검토(`INIT/APPROVE/REMAP`)·명시적 publish·activation commit까지 구현했다. 로드맵 Phase 8 8개 항목을 기존 보안 계약을 유지하는 작은 단계로 나눈다. 순서는 위험이 작고 기존 immutable 구조를 그대로 쓰는 것부터다.

| 단계 | 로드맵 항목 | 내용 | 전제 |
| --- | --- | --- | --- |
| 8-01 | mapping publish·rollback 화면 | 버전 이력 표시, 이전 immutable 버전 재활성화, 활성 기록 | 이번 단계 |
| 8-02 | schema fingerprint 비교 | 같은 논리 DB의 새 등록(후속 파일) 또는 PostgreSQL profile 재검사 결과를 기존 활성 mapping과 table·column 단위로 비교 | 등록 불변성 유지(덮어쓰기 대신 새 등록) |
| 8-03 | 영향받은 mapping `STALE` 처리 | 비교 결과 영향 field만 재검토 대상으로 표시, 영향 없는 field 승계 제안(자동 publish 없음) | 8-02 |
| 8-04 | test query·결과 미리보기 | **publish된** mapping에서만 고정 합성 test QueryIR 실행 미리보기(행 수 제한·redaction) | 미게시 mapping 실행 금지 유지 |
| 8-05 | verified query 자동 회귀 | 승인된 test query 집합을 publish·재활성화 전 자동 실행해 결과 변화 표시 | 8-04 |
| 8-06 | 코드값·관계 교정 | ontology value mapping·관계 교정 계약 | 설계 ADR 선행, L2 전 실제 DB 금지 |
| 8-07 | DB 등록·스캔 화면 통합 | 메인 `DB 변경` 안의 prototype 흐름을 별도 관리자 화면으로 모음 | 8-01~8-05 |

실제 학교 DB mapping publish·질의는 계속 L2 전 금지다. 모든 단계는 합성 입력으로만 검증한다.

## 8-01 범위

### 1. storage (`backend/adaptive/registration/reviews.py`)

- `activation.json`이 유일한 실행 권한 기록이라는 기존 계약을 유지한다. `current.json`은 최신 publish 버전 포인터로 남기고 재활성화에서 바꾸지 않는다.
- `list_mapping_versions(repository_root, database_id)`: `mapping-vNNNN.json`을 버전 순으로 strict 읽고 버전별 `version`, `published_by`, `published_at`, `activatable`(아래 재활성화 검증 통과 여부), `active`(activation 버전과 같음), `latest`(current 버전과 같음)를 반환한다. 최근 50개까지만. 파일 원문·물리 이름·경로는 반환하지 않는다.
- `activate_mapping_version(repository_root, database_id, mapping_version, activated_by)`:
  - 입력 검증(database ID·버전 1~9999·reviewer), 등록 strict reload, per-DB lock.
  - 한 번이라도 publish된 상태(`current.json`·`activation.json`·draft 존재)가 아니면 `ARTIFACT_MISMATCH`.
  - 대상 mapping 파일을 strict 읽고 `database_id`·`version`·`PUBLISHED`·등록 schema fingerprint·현재 ontology version·canonical 전체 coverage·catalog 실행 가능성을 publish와 같은 기준으로 재검증한다. 실패는 `PUBLISH_BLOCKED`.
  - 이미 같은 버전이 활성이면 쓰지 않고 그대로 반환한다(idempotent).
  - 새 `ActivationManifest`(대상 mapping SHA-256·bytes, 등록 content hash·label)를 원자적으로 교체하고 `load_activation`으로 재검증한 결과를 반환한다.
- 활성 기록: `ActivationManifest`에 선택 필드 `activated_by`, `activated_at`(UTC ISO 8601)을 추가한다. 기존 manifest(필드 없음)도 그대로 유효하다. publish는 `activated_by=published_by`로 기록한다.
- publish idempotent 경로 보완: draft가 최신 `current`와 같아 새 버전을 만들지 않을 때, 활성 버전이 다르면(재활성화 뒤 또는 current·activation 사이 중단) 최신 버전을 재활성화 검증 뒤 활성화한다. "publish를 누르면 최신 검토 결과가 실행된다"를 보장한다.

### 2. HTTP (`backend/adaptive/api/review_publish.py`)

- `POST` action `ACTIVATE`: `{"action","database_id","mapping_version","activated_by"}` strict body. 성공 응답은 publish 응답과 같은 형태(`status: PUBLISHED`, `mapping_version`=활성 버전, `registry_eligible: true`). 실패는 기존 고정 code(`PUBLISH_BLOCKED`는 fields 없이).
- `GET` detail에 게시된 DB이면 `active_mapping_version`(activation strict load 성공 시 버전, 실패 시 null)과 `mapping_versions` 목록을 추가한다. 미게시 detail shape는 바꾸지 않는다.
- registry·catalog·clarification은 이미 요청마다 activation을 strict reload하므로 추가 변경 없이 재활성화 버전을 쓴다.

### 3. frontend (`src/adaptive-prototype`)

- `mappingReview.ts`: detail parser가 새 두 필드를 strict 검증(버전 정수, 중복 없음, active 최대 1개이고 `active_mapping_version`과 일치, latest는 `mapping_version`과 일치, 불리언 타입), `activateMappingVersion` client, `ACTIVATE` 응답 parser.
- `MappingReviewPanel.tsx`: 게시된 DB에서 버전 이력(버전·게시자·게시 시각·활성/최신 표시)과 활성이 아닌 `activatable` 버전의 "이 버전으로 되돌리기" 버튼. 버튼은 2단계 확인(선택 → "재활성화 확인")을 거치고, 검토자 입력이 필요하다. 성공 뒤 detail을 다시 불러오고 `onPublished`로 registry 갱신을 알린다.
- legacy 화면은 바꾸지 않는다.

## 보안 계약(유지)

미게시 draft 실행 금지, 명시적 publish·재활성화만 실행 권한 변경, immutable mapping 파일 불변, 등록 파일 불변, 실행 전 strict reload, browser에 물리 경로·SQL·행·sample·traceback 비노출, 고정 오류 code, 요청 body 64 KiB·duplicate key 거부.

## 테스트

- storage: publish v1→remap→publish v2→v1 재활성화→`load_activation` v1·`current` v2 유지; 같은 버전 재활성화 idempotent(파일 미변경); 없는 버전·0·문자열·범위 밖 거부; 변조된 대상 파일(schema fingerprint·coverage·JSON 손상) `PUBLISH_BLOCKED`/`ARTIFACT_MISMATCH`이고 기존 활성 유지; 미게시 DB 재활성화 거부; 재활성화 뒤 같은 draft publish 시 최신 버전 재활성화; 기존 `activated_by` 없는 manifest 로드; 이력 목록 필드·순서·active/latest.
- HTTP: ACTIVATE strict body(extra key·타입·duplicate key 거부), 성공 응답 shape, registry가 재활성화 버전을 사용, GET 새 필드, 오류 응답에 경로·원문 없음.
- frontend(node --test): parser 새 필드 검증·거부 사례, `activateMappingVersion` 요청 body와 응답 검증, 기존 detail(미게시) 호환.
