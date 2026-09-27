# Phase 6-18 구현 지침: loopback SQLite upload와 검토 대기 요약

## 목표

메인 화면의 `DB 변경` modal에서 사용자가 standalone SQLite 파일을 선택하고, 제한된 loopback HTTP upload 경계를 통해 기존 Phase 6-17 private registration을 호출한다. 성공한 등록은 계속 `PENDING_REVIEW`로 격리하며, schema catalog와 mapping proposal의 표본값 없는 요약만 읽기 전용으로 표시한다. 등록 직후 course catalog나 clarification 실행 DB로 선택할 수 없어야 한다.

## 완결 범위

```text
DB 변경 modal
→ database ID·label·SQLite 파일 선택
→ 제한된 raw-body loopback upload
→ 임시 파일 cleanup 보장
→ Phase 6-17 register_database()
→ Phase 6-17 load_registration() strict reload
→ PENDING_REVIEW schema/mapping 요약 반환
→ modal 안 읽기 전용 표시
```

- 기존 synthetic prototype FastAPI app에 등록 전용 POST endpoint를 추가한다. endpoint는 raw SQLite body를 streaming으로 읽고 `database_id`와 `label`만 명시적으로 받는다. multipart parser가 route 진입 전에 무제한 spool하지 않도록 multipart upload를 사용하지 않는다.
- `Content-Type`은 고정 SQLite upload media type만 허용한다. 누락·중복 query parameter, 예상하지 않은 query parameter, 비어 있는 body, 잘못된 `Content-Length`, 32 MiB 초과 선언 또는 실제 streaming 초과를 fail closed한다.
- request body를 한 번에 메모리에 적재하지 않는다. 제한된 chunk로 exclusive 임시 regular file에 쓰며 32 MiB를 HTTP 경계에서도 강제한다. 성공·실패·client disconnect 모두 임시 파일을 정리한다. 임시 경로나 원래 filename은 응답·로그·예외에 노출하지 않는다.
- HTTP boundary는 기존 `backend.adaptive.registration.register_database()`와 `load_registration()`을 그대로 호출한다. 별도 SQLite header/schema/mapping 검사나 약한 loader를 만들지 않는다.
- 등록 성공 응답은 strict reload 결과에서만 만든다. 상태는 정확히 `PENDING_REVIEW`여야 한다. 응답은 version, database ID, label, status, schema fingerprint와 sample 없는 schema/mapping proposal 요약만 포함한다. source path, private path, row/sample value, SQL, SQLite 상세 오류, traceback을 포함하지 않는다.
- schema 요약은 table/column/FK 개수와 읽기 전용 table 구조 요약만 허용한다. mapping 요약은 proposal의 semantic field, 검토 상태, 상위 후보의 물리 table/column·confidence/reasons처럼 이미 proposal에 있는 metadata만 허용한다. 새 자동 승인·교정·publish 판단을 만들지 않는다.
- 모든 HTTP 실패는 고정 공개 code와 고정 status mapping만 반환한다. Phase 6-17 `RegistrationErrorCode`의 원문 예외나 내부 message를 반사하지 않는다. validation, OS, SQLite, catalog, mapping, JSON 오류도 redacted generic code로 닫는다.
- endpoint 성공 전후 `/api/adaptive/v1/databases` 응답은 byte-equivalent semantic content를 유지한다. 새 registration ID는 Phase 6-16 `SyntheticDatabaseRegistry`, course catalog, clarification loader/executor에 추가하지 않는다.
- 메인 `DB 변경` modal에 별도 `SQLite 등록(검토 대기)` 영역을 둔다. database ID, label, `.sqlite`/`.sqlite3` file 입력과 등록 button을 제공한다. extension은 UX 힌트일 뿐 backend 신뢰 근거가 아니다.
- frontend client는 raw `File` body를 전송하고 strict 성공/error parser를 사용한다. 예상하지 않은 key/type/status/version은 contract 오류로 처리한다. 서버 body·path·SQL·상세 message를 사용자 문자열에 반사하지 않는다.
- 업로드 중 중복 submit을 막고 modal close/unmount에서 request를 abort한다. 새 시도는 이전 결과·오류를 명시적으로 교체하며 stale response가 최신 UI를 덮지 못하게 한다.
- 성공 시 active/pending DB 선택, sessionStorage, course list, 선택 강의, clarification link를 바꾸지 않는다. registry refetch로 등록 DB를 선택 가능하게 만들지 않는다. 성공 요약에는 `검토 전에는 조회·질의에 사용할 수 없음`을 명시한다.
- mapping 수정·승인·publish, 실행 registry 편입, 등록 목록 관리·삭제는 구현하지 않는다.

## 보안·격리 계약

- Phase 6-17의 32 MiB 제한, standalone SQLite 검사, Git ignored private storage, immutable/no-overwrite, read-only immutable schema 검사, sample 없는 artifact, strict reload를 약화하지 않는다.
- `PENDING_REVIEW` 이외 상태를 성공 응답으로 반환하지 않는다.
- Phase 6-16 fixed registry와 Published Mapping만 실행 권한을 가진다. registration 성공은 권한 상승이 아니다.
- loopback CLI host `127.0.0.1`, reload off, access log off 계약을 유지한다. CORS나 외부 bind를 추가하지 않는다.
- legacy `/api/v1/courses`와 기존 메인 화면 legacy 흐름을 변경하지 않는다.
- 실제 CNU 자료, PostgreSQL, Ollama live 호출을 사용하지 않는다.

## 테스트

새 backend 집중 테스트에서 최소 다음을 검증한다.

1. 합성 standalone SQLite raw upload 성공, `PENDING_REVIEW` strict summary, sample/path/SQL 비노출.
2. 등록 전후 Phase 6-16 database registry 동일, 새 ID의 catalog·clarification 접근 거부.
3. empty, 잘못된 content type/parameter/content length, 선언·실제 32 MiB 초과, malformed/corrupt/sidecar 성격 입력, 기존 ID 충돌의 고정 HTTP mapping.
4. registration/strict reload 실패와 예상 밖 예외의 redaction, 임시 파일 cleanup.
5. duplicate/unknown parameter와 response contract extra field를 fail closed.

새 frontend 집중 테스트에서 최소 다음을 검증한다.

1. strict response/error parser와 raw File request 계약.
2. idle/uploading/success/error/retry 상태 전이, 중복 submit 차단, abort/stale 차단.
3. 성공 후 active/pending selection과 sessionStorage·course UI를 바꾸지 않고 `PENDING_REVIEW` 요약만 표시.
4. 등록 DB가 선택 radio와 clarification link에 추가되지 않음.
5. backend 상세 body나 예외 문자열 비노출.

OpenCode는 신규 및 직접 관련 Phase 6-16/17 backend·frontend 테스트, 변경 frontend lint/build만 실행한다. 전체 pytest, compileall, `git diff --check`, 문서 갱신, commit, push는 하지 않는다.

## 변경 금지

- legacy system, 실제 CNU 자료, PostgreSQL, Ollama live 호출, 기존 합성 fixture 내용을 변경하지 않는다.
- mapping review/edit/approve/publish, Phase 6-16 registry 편입, 실제 실행을 추가하지 않는다.
- 계획/ADR/change log/handoff는 구현 agent가 수정하지 않는다.

## 완료 보고

긴 diff와 전체 로그를 출력하지 않는다. 변경 파일, 핵심 계약, 실패 원인, 실행한 관련 테스트 결과, 남은 위험만 한국어로 간결히 보고한다. 모델별 사용량이나 통계는 조회·보고하지 않는다.
