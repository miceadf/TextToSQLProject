# Phase 6-07: 합성 영속 검토 큐 lifecycle

기존 `InMemoryReviewQueue`, `review-queue-snapshot-v1`, `AgentWorkflowCoordinator`를 이어서 **strict load/복원 → 합성 workflow 실행 → 변경된 큐를 새 immutable snapshot에 게시**하는 한 기능 흐름을 구현한다. 기존 snapshot을 덮어쓰거나 실제 운영 DB·UI·관리자 승인 흐름을 만들지 않는다. 실제 Ollama·PostgreSQL·CNU 자료·private recording bundle을 사용하지 않는다.

## 먼저 확인

- 시작 시 `git status --short`와 최근 commit을 확인하고 기존 변경을 보존한다.
- `AGENTS.md`, `docs/next-session-handoff.md`, 계획 문서의 Phase 6·다음 시작점·ADR-029~034, `docs/human-validation-checkpoints.md`의 E3/L1/L3/L5를 따른다.
- `backend/adaptive/harness/review_queue.py`, `review_queue_storage.py`, `agent_workflow.py`, recording bundle의 private path validator와 관련 테스트만 읽는다. legacy `backend/llm.py`, FastAPI/React 경로는 변경하지 않는다.

## 구현 계약

1. strict-loaded `ReviewQueueSnapshot`에서 새 `InMemoryReviewQueue`를 복원하는 좁은 공개 경계를 추가한다. snapshot entry 순서, idempotency key, `(model_run_id_sha256, request_id_sha256)` identity를 그대로 보존한다. raw run/request ID, `RepairSummary`, 질문, SQL, 행을 역추정하지 않는다.
2. 복원은 caller가 만든 Pydantic instance를 그대로 신뢰하지 않는다. plain JSON으로 dump 후 재검증하고 기존 snapshot의 code/stage/disposition/idempotency/count/hash 계약을 가능한 단일 validator에서 재사용한다. 위조 entry, 중복 identity/key, 잘못된 version은 고정 오류로 fail closed한다. 복원 실패 시 부분 queue를 반환하거나 변경하지 않는다.
3. 작은 durable lifecycle coordinator를 추가한다. 입력은 현재 snapshot 경로(없으면 명시적 empty 시작), 다음 snapshot 경로, repository root와 기존 `AgentWorkflowCoordinator.run()` 입력이다. 실행 전 현재/다음 경로, 서로 다른 경로, private·Git ignored/tracked·symlink/junction/reparse/hardlink 정책, 다음 경로 no-overwrite를 검사한다.
4. 현재 snapshot이 있으면 strict load 후 queue를 복원하고, 없으면 새 empty queue를 만든다. 그 queue를 기존 `AgentWorkflowCoordinator`에 주입한다. caller가 queue, `CritiqueResult`, disposition, 복원 entry를 직접 주입해 lifecycle 불변식을 우회하는 API는 두지 않는다.
5. workflow 결과가 `ENQUEUED`일 때만 변경된 queue를 다음 **새** snapshot에 원자 게시한다. `ALREADY_PRESENT`와 `NOT_ELIGIBLE`이면 새 snapshot을 쓰지 않는다. overwrite, in-place update, current-pointer 교체, 자동 retry를 구현하지 않는다.
6. 반환 계약은 version, 기존 `AgentWorkflowResult`, persistence 상태(`WRITTEN` 또는 `UNCHANGED`), 가능한 경우 새 snapshot의 content hash 같은 비식별 metadata만 포함한다. raw local path, 질문, SQL, 행, 물리 mapping, secret, raw ID를 새 결과·오류·stdout·기본 로그에 넣지 않는다. 기존 `HarnessResult` 반환 계약은 바꾸지 않는다.
7. 경로/복원/type 오류는 모델·repair provider·executor 호출 전에 고정 code로 차단한다. 실행 뒤 snapshot 게시 실패는 고정 storage code로 fail closed한다. 이 경우 읽기 전용 모델/실행 side effect는 이미 발생했고 호출자에게 반환되지 않은 in-memory queue 변경은 process-local로 남을 수 있으나 기존 source snapshot과 기존 target bytes는 보존됨을 문서화한다.
8. 같은 source snapshot과 같은 run/request/result 재실행은 기존 entry를 찾아 `ALREADY_PRESENT`·`UNCHANGED`가 되어야 한다. 같은 identity의 다른 결과는 기존 queue conflict로 실패하고 source snapshot·target을 바꾸지 않는다.
9. 기존 `review-queue-snapshot-v1`, `review-queue-v1`, `agent-workflow-v1`, `ExecutionHarness`, `BoundedRepairCoordinator.run()`, critique taxonomy와 호출 예산을 깨지 않는다. 새 snapshot schema가 꼭 필요하지 않으면 추가하지 않는다.

## 합성 테스트

- empty 시작 + eligible failure: workflow 호출 예산 유지, `ENQUEUED`, next snapshot 1개 게시, strict load 후 entry 정확히 1개.
- 기존 snapshot + 새 eligible failure: 기존 순서/entry 보존, 새 entry 뒤에 추가, source bytes 불변, next snapshot에 2개.
- 기존 entry 동일 재등록: `ALREADY_PRESENT`, `UNCHANGED`, next 파일 생성 없음.
- success·0행 success·`REQUEST_INVALID`: `NOT_ELIGIBLE`, `UNCHANGED`, next 파일 생성 없음.
- 같은 run/request의 다른 결과: conflict, source 불변, next 파일 없음.
- 위조 snapshot/model instance, duplicate identity/key, hash/count/version/entry semantic mismatch: 모델/provider/executor 0회, 부분 복원 없음.
- source=target, tracked/non-ignored/outside path, symlink/junction/reparse/hardlink, 이미 존재하는 target: 실행 전 차단. 기존 private path validator를 재사용하고 약한 복제 validator를 만들지 않는다.
- 게시 직전 target 생성, write/link/fsync 계열 실패: 기존 source/target 보존, private temp path·secret 비노출, 고정 오류. 실제 filesystem 보장 범위를 과장하지 않는다.
- 반환 객체와 복원 queue의 방어적 복사. 파일·네트워크·Ollama·PostgreSQL 호출 없음.
- 관련 테스트만 실행한다. 전체 pytest/compileall/diff-check는 Codex 독립 검토와 수정 뒤 별도로 한 번씩 실행하므로 여기서 실행하지 않는다.

## 보고 형식

긴 diff·전체 로그 금지. 변경 파일, 핵심 계약, 관련 테스트 수와 결과, 실패 원인, 남은 위험만 요약한다. 계획·ADR·체크리스트·handoff·commit은 Codex가 독립 검토 후 처리하므로 수정하지 않는다.
