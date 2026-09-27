# Phase 6-19 구현 지침: private mapping 검토·publish와 실행 registry 활성화

## 목표

Phase 6-18에서 등록한 합성 standalone SQLite의 `PENDING_REVIEW` mapping proposal을 메인 `DB 변경` 흐름에서 명시적으로 검토·수정하고 publish한다. strict publish가 성공한 registration만 Phase 6-16 registry에 편입해 course catalog와 clarification에서 선택·실행할 수 있게 한다. registration 성공, draft 생성, 일부 field 승인만으로는 실행 권한이 생기지 않는다.

## 완결 흐름

```text
strict PENDING_REVIEW registration
→ private review draft 최초 생성
→ field별 candidate 승인 또는 catalog column으로 remap
→ 모든 prototype 필수 semantic field 검토 완료
→ schema/ontology/fingerprint/physical column 재검증
→ immutable Published Mapping 게시
→ strict published activation reload
→ Phase 6-16 registry에 private entry 노출
→ 같은 published mapping으로 catalog·clarification read-only 실행
```

이번 단계는 완전 합성 flat 또는 legacy-schema standalone SQLite로 end-to-end 검증한다. 실제 CNU 자료나 실제 학교 mapping을 사용하지 않으며 L2를 승인 처리하지 않는다.

## backend review·storage 계약

- 기존 `MappingReviewService`, `MappingDraft`, `PublishedMappingProfile`, `to_harness_mapping()`, default ontology를 재사용한다. 병렬 mapping 판단이나 자동 승인을 만들지 않는다.
- review state는 immutable Phase 6-17 registration 디렉터리를 수정하지 않고 별도 Git ignored private root(예: `.schema-adaptive/registration-reviews/<database_id>`)에 둔다.
- private root containment, Git ignored/untracked, redirect/reparse/hardlink 방어는 Phase 6-17과 같은 강도로 적용한다. source/private path와 내부 예외는 공개 응답에 노출하지 않는다.
- draft init은 strict `load_registration()`의 proposal/catalog에서만 생성한다. 동일 registration에 대한 같은 init은 idempotent해야 하며 기존 review를 덮어쓰지 않는다. registration fingerprint·ontology가 달라졌으면 fail closed한다.
- field mutation은 server-owned proposal/catalog/draft를 매번 strict reload한 뒤 기존 review service를 호출한다. caller는 draft 전체, proposal, catalog, status, published mapping을 주입할 수 없다.
- 허용 action은 이번 UI 흐름에 필요한 `INIT`, `APPROVE`, `REMAP`, `PUBLISH`로 제한한다. `APPROVE`는 proposal candidate index만 받는다. `REMAP`은 strict registration catalog에 실제 존재하는 table/column만 받으며 reason을 요구한다. reviewer/published_by는 짧은 비공백·제어문자 없는 synthetic-local 식별자만 받는다.
- database ID별 cooperating lock으로 init/mutation/publish를 직렬화한다. mutation은 strict old state에서 새 draft를 원자 교체하고 실패 시 기존 bytes를 보존한다.
- publish는 기존 `MappingReviewService.publish()` 검증에 더해 Phase 6-16 canonical course DTO의 15개 `CANONICAL_SELECT` field가 모두 Published Mapping에 정확히 존재하는지 요구한다. 일부 field ignore/reject, pending, duplicate physical column, stale fingerprint, ontology drift, 없는 column, ambiguous/disconnected JOIN은 활성화하지 않는다.
- publish version은 server가 결정한다. immutable `mapping-vNNNN.json`과 atomic current pointer/activation manifest를 manifest-last 방식으로 게시한다. 같은 publish retry는 idempotent하거나 fixed conflict로 닫고 기존 published bytes를 덮어쓰지 않는다.
- strict activation loader는 registration strict reload, review artifact 허용 파일 집합·bounded exact JSON·hash/version, Published Mapping contract, database/schema/ontology fingerprint, physical relation graph와 canonical 15-field 실행 가능성을 다시 검증한다.
- process restart 뒤에도 strict published activation만 registry에 복원한다. malformed/tampered/stale/incomplete activation 하나가 있으면 해당 ID만 숨기고 fixed registry 동작과 다른 valid activation은 유지한다. 임의 디렉터리나 draft만 존재하는 ID는 노출하지 않는다.

## HTTP 계약

- 기존 loopback synthetic prototype에 작은 review API를 둔다. GET detail과 strict JSON action POST 정도의 최소 surface를 사용한다.
- GET은 draft/published review metadata, semantic field, 현재 decision, proposal candidates, remap에 허용되는 catalog table/column metadata만 반환한다. sample value, row, SQL, path, SQLite detail, raw exception은 반환하지 않는다.
- POST body는 `extra=forbid`, strict type, action별 discriminated contract를 적용한다. duplicate JSON key, unknown action/field/candidate/column, 과도한 문자열, status·version·draft injection을 거부한다.
- 외부 오류는 payload 없는 fixed code와 고정 HTTP status만 반환한다. publish block은 field별 내부 message를 그대로 반사하지 말고 고정 code와 안전한 미완료 field ID 목록 정도만 허용한다.
- publish 응답은 strict activation reload 뒤에만 `PUBLISHED`와 registry eligibility를 표시한다. response 생성 실패나 reload 실패 시 실행 registry에 노출하지 않는다.

## Phase 6-16 실행 registry 연결

- fixed flat·legacy synthetic entries와 legacy baseline을 그대로 보존한다. private published entry는 `schema: "private"` 같은 명시적 variant로만 추가한다. backend와 frontend registry parser를 함께 strict 갱신한다.
- registry snapshot은 server-owned fixed entries + strict valid activations로 결정적으로 정렬한다. caller action body가 registry entry나 label/schema를 직접 주입하지 못한다.
- private entry의 DB path는 strict registration loader가 확인한 `database.sqlite3`만 사용한다. 등록 source path나 UI file object를 재사용하지 않는다.
- catalog와 clarification은 동일한 activated database ID, strict Published Mapping, schema fingerprint, default ontology를 사용한다.
- private mapping은 기존 `SchemaAdaptiveSQLiteCompiler`, physical relation graph, `SchemaAdaptiveAstPolicy`, `SQLiteReadOnlyExecutor`를 사용한다. caller SQL/path/mapping 주입 금지. JOIN ambiguity·schema drift·mapping drift·invalid canonical row는 redacted fail closed한다.
- clarification의 deterministic provider 범위는 기존 `Course.name`·`Instructor.name` 그대로 유지한다. 새 모델·Ollama 호출을 추가하지 않는다.
- publish 전 ID, draft-only ID, tampered/stale activation은 `/databases`·catalog·clarification 모두에서 계속 미노출/실행 불가다.

## frontend 상태 전이

- Phase 6-18 upload 성공 요약 아래 `mapping 검토 시작`을 제공한다. strict INIT/GET 성공 뒤 field별 current decision과 candidates를 읽기 전용 metadata로 표시한다.
- reviewer 입력, candidate 승인, table/column remap+reason, publish button을 제공한다. 임의 JSON·path·SQL 입력 UI는 만들지 않는다.
- action 중 중복 submit 차단, modal close/unmount abort, operation generation으로 stale response 차단을 적용한다. 새 action은 이전 오류를 교체하되 성공한 server state를 추측해 optimistic publish하지 않는다.
- publish 성공 뒤 database registry를 다시 불러오고, strict registry 응답에 새 ID가 실제 포함된 경우에만 선택 radio를 표시한다. 자동 active 선택·sessionStorage 변경·course UI 초기화는 하지 않는다.
- publish 전에는 기존 upload summary와 review state가 있어도 radio/clarification link를 만들지 않는다.
- 오류는 고정 한국어 문구만 표시하고 backend body/detail/path/SQL을 반사하지 않는다.

## 테스트

backend 집중 테스트에서 최소 다음을 검증한다.

1. strict registration → init → 15 field approve/remap → publish → strict activation round-trip.
2. registration 직후·draft init·일부 승인·publish 실패까지 registry/catalog/clarification 미노출.
3. publish 후 registry에 정확히 한 번 노출, catalog canonical 6행과 clarification 기존 두 option 중 선택 결과 실행.
4. process restart rehydrate 후 같은 private entry와 결과 유지.
5. stale schema, registration/proposal/draft/published/activation 변조·추가/누락, ontology mismatch, 없는/중복 column, ambiguous/disconnected JOIN을 fail closed하고 registry에 숨김.
6. duplicate/unknown JSON key/action, forged status/version/draft/mapping/path/SQL 주입, concurrent same-ID mutation/publish, storage failure의 fixed redaction과 기존 state 보존.
7. fixed flat·legacy registry/catalog/clarification과 legacy baseline 회귀 없음.

frontend 집중 테스트에서 최소 다음을 검증한다.

1. review detail/action/publish strict parser와 fixed error redaction.
2. idle/loading/action/publish/success/error/retry, duplicate 차단, abort/stale 차단.
3. publish 전 radio·clarification link 미노출, 성공 후 registry refetch 결과에 있을 때만 표시.
4. publish 성공이 active DB/sessionStorage/course UI를 자동 변경하지 않음.
5. `schema: private` registry strict 수락과 unknown variant 거부.

OpenCode는 신규 및 직접 관련 Phase 6-16/17/18 mapping·registration·registry backend/frontend 테스트, 변경 frontend lint/build만 실행한다. 전체 pytest, compileall, `git diff --check`, 문서 갱신, commit, push는 하지 않는다.

## 변경 금지

- legacy system, 실제 CNU 자료, PostgreSQL, Ollama live 호출, 기존 합성 fixture 내용을 변경하거나 사용하지 않는다.
- 자동 approve/publish를 만들지 않는다. `AUTO_APPROVE_CANDIDATE`도 사용자 action 전에는 `PENDING`이다.
- 실제 학교 DB publish·질의에 필요한 L2를 완료 처리하지 않는다.
- 계획/ADR/change log/handoff는 구현 agent가 수정하지 않는다.

## 완료 보고

긴 diff와 전체 로그를 출력하지 않는다. 변경 파일, 핵심 계약, 실패 원인, 관련 테스트 결과, 남은 위험만 한국어로 간결히 보고한다. 모델별 사용량·통계는 조회·보고하지 않는다.
