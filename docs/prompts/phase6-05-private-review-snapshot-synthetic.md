# Phase 6-05: 합성 검토 큐의 private snapshot 저장·strict load

Phase 6-04 `InMemoryReviewQueue`의 **고정 메타데이터 항목만** 명시적 private 파일에 저장하고 strict load하는 작은 합성 경계를 구현해줘. 실제 운영 큐, 관리자 UI, 실제 CNU 질문·응답·DB 행, live 모델 호출은 이번 단계에 없다. 영속 snapshot은 관리자 승인이나 교정 결과가 아니다.

## 먼저 확인

- 시작 시 `git status --short`, 최근 Conventional Commit을 확인하고 미커밋 변경을 보존한다.
- `AGENTS.md`, `docs/schema-adaptive-text-to-sql-plan.md`의 Phase 6·ADR-030/031/032·다음 시작점, `docs/next-session-handoff.md`, `docs/human-validation-checkpoints.md`의 E2/E3/L1/L3/L5를 읽는다.
- `backend/adaptive/harness/review_queue.py`, `recording_bundle_storage.py`의 repository/private path 방어·원자 게시·strict load, `recording_bundle_contracts.py`, 해당 테스트를 읽는다. 기존 recording bundle 저장·legacy 시스템은 변경하지 않는다.

## 저장 계약

1. 별도 `review_queue_storage` 모듈을 둔다. 명시적인 `repository_root`와 `.schema-adaptive` 아래 private output **파일 경로**, `InMemoryReviewQueue`를 받아 snapshot을 쓴다. source는 `queue.list_entries()`의 고정 필드만 허용한다. 질문·SQL·행·원문 예외·물리 mapping·raw run/request ID·경로·secret을 파일 내용·stdout·기본 로그·통제 오류에 넣지 않는다. 새로운 수집·enqueue·모델·DB 실행 경로는 만들지 않는다.
2. versioned 단일 JSON snapshot에 queue version, 항목 수, 순서 있는 항목, **hash 필드를 제외한 payload의 canonical JSON bytes SHA-256**을 넣는다. UTF-8, `sort_keys=True`, compact separators, `ensure_ascii=False`, `allow_nan=False` 규칙을 문서화·테스트한다. 내용 hash는 변조 감지일 뿐 작성자 인증이 아님을 명시한다. strict loader는 duplicate key, 비유한 값, 추가/누락 필드, count/hash 불일치, 중복 run/request 조합, 중복 idempotency key, 잘못된 code·stage·disposition·version을 고정 오류로 거부한다. loader는 파일 안의 경로를 신뢰하지 않는다.
3. 저장 전 private 경로·Git ignored/tracked·symlink/junction/reparse·hardlink 정책을 **기존 recording bundle의 동일 validator를 재사용**해 검사한다. 새 코드에서 별도의 약한 경로 검사 복제본을 만들지 않는다. 필요한 최소 공개 wrapper만 추가할 수 있지만 기존 bundle 계약·동작은 보존한다. 파일과 부모 경로의 redirect/escape를 막는다. 경로·OS 예외 원문은 고정 오류 code로 변환한다.
4. no-overwrite가 기본값이다. 같은 path에 기존 snapshot이 있으면 이전 바이트를 보존하고 고정 오류로 거부한다. 임시 파일 fsync 후 원자 게시하고 실패 시 임시 파일을 정리한다. `overwrite`·in-place update·실제 운영 동시성/잠금은 이번 단계에서 구현하지 않는다. 빈 큐 snapshot도 명시적 저장이면 허용하되 내용은 고정 메타데이터만 유지한다.
5. strict load 결과는 검증된 snapshot 계약/항목의 방어적 복사본만 반환하고 내부 queue를 자동 수정하지 않는다. 실제 운영 영속 큐 저장·관리자 UI·승인 workflow 체크박스는 미완료로 둔다.

## 합성 테스트

- 합성 queue의 성공 미등록 + 실패 2~3건을 snapshot 저장→strict load하면 순서·고정 code/stage/disposition·hash가 일치. 빈 queue도 검증.
- 같은 입력은 같은 canonical bytes/hash. 항목 순서 또는 고정 metadata 변경은 hash 변경. 잘못된 count/hash·중복 JSON key·비유한 값·중복 ID/key·미지 code/stage/disposition/version·추가 필드 strict 거부.
- private 경로 밖·Git tracked/unignored·symlink/junction/reparse/hardlink·기존 파일 덮어쓰기·게시 실패 시 이전 파일 보존과 임시 파일 정리. 기존 bundle path/storage 테스트 회귀 유지.
- 원시 질문·SQL·행·비밀형 ID·로컬 경로가 저장 바이트·결과·예외·stdout·기본 로그에 없음. provider/model/DB/network 호출 0회. 실패는 fixed code만.
- 관련 테스트 먼저, 마지막에 `.venv/Scripts/python.exe -m pytest tests -q`, `.venv/Scripts/python.exe -m compileall -q backend tests`, `git diff --check`. 기본 Windows temp ACL 실패와 접근 가능한 임시 경로 재실행을 구분해 보고한다.

## 문서·보고

- 실제 완료 범위만 plan Phase 6 본문·체크박스·다음 시작점·변경 이력에 반영하고 중요한 storage 결정은 ADR로 남긴다. `docs/next-session-handoff.md` 갱신. `운영 실패를 관리자 검토 큐에 저장` 전체 항목은 실제 운영 연결 전까지 완료 표시하지 않는다.
- 40건·30건의 `NEEDS_HUMAN_REVIEW`와 E3/L5·L1/L3 대기를 유지하고 지금 팀 결정을 요청하지 않는다. 실제 CNU 자료, private recording bundle, Ollama, PostgreSQL, FastAPI/React, 모델 점수는 사용하지 않는다.
- 변경 파일, 테스트, 실제 쓰기 경로가 합성 private인 점, 남은 위험·미구현 사항과 코드 품질 징후를 보고한다. Conventional Commit으로 커밋하되 push는 하지 않는다.
