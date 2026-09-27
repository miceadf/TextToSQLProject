# Phase 6-17 구현 지침: private SQLite 등록 경계

## 목표

사용자가 제공한 독립형 SQLite 파일을 backend core가 안전하게 검증·복사하고, 표본값 없이 schema catalog와 rule-based mapping proposal을 생성해 private storage에 하나의 immutable registration으로 게시한다. 이 단계의 결과는 `PENDING_REVIEW`이며, 사람 검토와 명시적 publish 전에는 Phase 6-16 database registry, course catalog, clarification 실행 경로에서 선택하거나 질의할 수 없다.

## 범위

- 새 `backend/adaptive/registration` package에 strict contract, 고정 오류 코드, storage/service를 둔다.
- public core API는 repository root, source SQLite path, `database_id`, display label을 받아 등록하고 strict load할 수 있어야 한다. HTTP upload endpoint와 브라우저 UI는 만들지 않는다.
- 기본 private root는 `.schema-adaptive/registered-databases`다. 기존 recording bundle의 Git root·ignored/private containment·symlink/junction/reparse 방어 원칙을 재사용하거나 같은 강도의 공용화된 helper로 사용한다. 기존 API를 약화하지 않는다.
- `database_id`는 Phase 6-16과 같은 정규화된 소문자 `^[a-z0-9][a-z0-9-]{0,63}$`만 허용하고 Windows 예약 장치명과 trailing dot/space를 거부한다. 기존 합성 registry ID와 충돌해도 거부한다. label도 공백-only, 제어문자, 과도한 길이를 거부한다.
- source는 regular file이어야 하며 symlink/junction/reparse를 거부한다. 빈 파일과 32 MiB 초과 파일을 거부하고, 확장자만 신뢰하지 말고 SQLite header와 실제 read-only immutable open/`PRAGMA quick_check`를 검증한다. `-wal`, `-shm`, `-journal` sidecar가 등록 전후에 있거나 복사본의 journal mode가 WAL이면 독립 snapshot이 아니므로 fail closed한다.
- source를 수정하거나 write-open하지 않는다. source는 한 번 연 binary fd에서 fstat/header/32 MiB 상한/streaming copy/SHA-256을 처리하고, 경로를 다시 열어 복사하지 않는다. 상한은 stat뿐 아니라 읽는 중에도 강제한다. 복사본 검사는 `mode=ro&immutable=1` 및 `PRAGMA query_only=ON`으로 sidecar 생성을 막는다. 필요하면 기존 `SQLiteAdapter`에 default-off immutable 옵션을 추가하되 기존 호출 동작은 바꾸지 않는다.
- `SQLiteAdapter`나 sqlite가 상세 메시지를 포함한 예외를 내더라도 registration 경계에서 원래 message/경로를 폐기하고 고정 오류 코드로만 재매핑한다. 오류 문자열·manifest에 source 절대경로, row/sample value, SQLite 상세 오류 payload를 노출하지 않는다.
- `SQLiteAdapter.inspect(..., include_samples=False)`, `load_default_ontology()`, `RuleBasedSchemaAligner.align()`을 사용한다. 새 병렬 schema/mapping 구현을 만들지 않는다.
- `registration.json` 자체를 manifest이자 commit marker로 정의한다. schema version, `PENDING_REVIEW` 상태, database ID/label, content hash/bytes, schema fingerprint, ontology version, 나머지 각 artifact의 상대 경로·hash·bytes를 기록한다. 허용 파일은 정확히 `database.sqlite3`, `catalog.json`, `proposal.json`, `registration.json` 네 개며 registration manifest는 staging 안에서 마지막에 쓴다.
- registration 디렉터리는 immutable/no-overwrite다. 같은 database ID 재등록은 기존 자료를 덮어쓰지 않는 고정 conflict로 닫는다. staging은 private root와 같은 volume 아래에 만들고, database ID별 `O_CREAT|O_EXCL` lock으로 cooperating writer를 직렬화한 뒤 final 부재를 재검증하고 staging directory를 단일 rename한다. lock과 staging은 성공·실패 모두 정리한다. 동시 등록도 최대 하나만 성공해야 한다.
- strict loader는 private containment, redirect와 지원 플랫폼의 hardlink, 허용 파일 집합, size/hash, Pydantic contract, database ID, catalog/proposal fingerprint·ontology 일치, 저장 DB 재검사의 schema fingerprint를 검증한다. 저장 후 변조·추가 파일·경로 이탈은 fail closed한다. hardlink link-count를 신뢰할 수 없는 플랫폼에서는 배타 생성·containment·reparse 검사가 주 방어선이다.
- 등록 성공만으로 Phase 6-16 fixed registry를 수정하지 않는다. 기존 `/api/adaptive/databases`, course catalog, clarification 동작과 legacy baseline은 그대로 둔다.

## 오류 계약

- 외부에는 payload 없는 고정 enum 문자열만 노출하는 전용 예외를 둔다. 최소 분류는 `INVALID_INPUT`, `UNSAFE_SOURCE`, `SOURCE_TOO_LARGE`, `INVALID_SQLITE`, `PRIVATE_STORAGE_INVALID`, `REGISTRATION_CONFLICT`, `ARTIFACT_MISMATCH`, `STORAGE_ERROR`, `INSPECTION_FAILED`, `MAPPING_FAILED`다.
- 내부 `sqlite3`, catalog, validation, OS, Git 검사 예외의 메시지나 경로를 그대로 연결하지 않는다.

## 테스트

새 집중 테스트에서 최소 다음을 검증한다.

1. 합성 flat/legacy 중 하나의 독립 SQLite를 등록하면 copy hash·catalog fingerprint·proposal이 결정적으로 일치하고 sample values/source path가 artifact와 예외에 없다.
2. strict load round-trip이 성공하고 상태가 `PENDING_REVIEW`이며 Phase 6-16 registry 응답/선택 가능 목록은 전후 동일하다.
3. empty, header 위조, corrupt DB, 초과 크기, sidecar 동반 source를 각 고정 오류로 거부한다.
4. source redirect와 private target redirect/path escape/Git tracked 또는 not-ignored 경계를 거부한다. 지원 환경에서는 hardlink도 거부한다.
5. 기존 ID 충돌, artifact 변조/누락/추가, DB schema 변조, manifest의 path/hash/fingerprint/ontology/ID 변조를 fail closed한다.
6. inspection/mapping/write 실패 후 published directory와 staging residue가 없고, 동시 동일 ID 등록은 하나만 성공한다.
7. 관련 catalog/mapping/Phase 6-16 backend 테스트만 실행한다. 전체 pytest, compileall, `git diff --check`, 문서 갱신, commit, push는 하지 않는다.

## 변경 금지

- legacy system, 기존 합성 fixture 내용, frontend, 실제 CNU 자료, PostgreSQL, Ollama live 호출을 변경하거나 사용하지 않는다.
- mapping review/publish를 자동 승인하지 않는다. `AUTO_APPROVE_CANDIDATE`는 제안 신호일 뿐 등록 상태를 publish로 바꾸지 않는다.
- 계획/ADR/handoff는 구현 agent가 수정하지 않는다.

## 완료 보고

긴 diff와 전체 로그를 출력하지 않는다. 변경 파일, 핵심 계약, 실행한 관련 테스트와 결과, 실패 원인/남은 위험만 한국어로 간결히 보고한다. 사용량 통계는 조회하거나 보고하지 않는다.
