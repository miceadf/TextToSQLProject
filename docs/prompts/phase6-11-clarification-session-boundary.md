# Phase 6-11: 합성 clarification session/API 경계

Phase 6-08~10의 **ambiguity 감지 → pending → 선택 constraint → 기존 read-only 실행**을 framework-neutral 메모리 session 하나로 연결한다. 실제 HTTP/FastAPI·React UI·운영 저장소·Ollama·PostgreSQL·CNU 자료는 제외한다. 이 단계의 목표는 bearer session의 만료·일회성·취소·동시 제출 경계를 합성 입력으로 닫는 것이다.

## 먼저 확인

- 시작 시 `git status --short`와 최근 commit을 확인하고 기존 변경을 보존한다.
- `AGENTS.md`, `docs/next-session-handoff.md`, 계획 문서 Phase 6·다음 시작점·ADR-031/036/037/038, `docs/human-validation-checkpoints.md`의 E3/L1/L3/L5를 따른다.
- `clarification.py`, `ambiguity_detector.py`, `clarification_query_constraint.py`, `clarification_execution_adapter.py`와 관련 합성 테스트만 읽는다. legacy FastAPI/React와 기존 Ollama/PostgreSQL 경로는 변경하지 않는다.

## 구현 계약

1. 별도 작은 모듈에 `clarification-session-v1`을 둔다. 메모리 coordinator는 `start(request, ontology, Published mapping, ruleset)`와 `submit(session_token, choice, provider, executor, 선택적 합성 compiler/policy)`의 두 공개 흐름만 제공한다. caller가 pending·signal·semantic ref·QueryIR·constraint·model·SQL·result·저장 record를 주입하는 API는 두지 않는다.
2. `start`는 모든 Pydantic 입력을 warning 비노출 plain JSON dump 후 재검증하고 깊은 snapshot을 만든 뒤 기존 detector/`prepare_detected_clarification()`을 호출한다. no-match는 `NOT_REQUIRED`이며 token/pending/options 없이 끝난다. 단일 match만 `PENDING`으로 저장하고 실제 choice에 그대로 제출할 수 있는 detector의 opaque option ID/semantic ref를 방어적 복사로 반환한다. 결과 validator는 각 selectable option ID를 기존 domain fingerprint로 변환했을 때 내부 `PendingClarification` option과 정확히 일치하는지 강제한다. multi-match·invalid context/ruleset은 기존 고정 오류를 그대로 전파하며 session을 만들지 않는다.
3. session token은 기본적으로 `secrets.token_urlsafe(32)` 이상의 예측 불가능한 opaque bearer 값이다. 내부 dict와 terminal tombstone에는 raw token 대신 domain-separated SHA-256만 저장한다. token과 request/question/database/physical mapping은 로그·오류에 반영하지 않는다. 합성 테스트용 clock/token factory 주입은 허용하되 결과 계약에 callable이나 호출 횟수를 넣지 않는다. token 충돌은 기존 session을 덮지 않고 고정 내부 오류로 닫는다.
4. coordinator 생성 시 양의 유한 TTL을 제한된 범위(예: 1초~1일)로 고정한다. deadline은 주입 monotonic clock으로 계산한다. `submit` 시 deadline 이상이면 active record를 제거하고 `EXPIRED` tombstone을 남긴 뒤 고정 `CLARIFICATION_SESSION_EXPIRED`로 거부한다. 만료 전에는 연장하지 않는다. 실제 wall-clock timestamp나 raw token을 결과에 저장하지 않는다.
5. `submit`은 token 형식과 `ClarificationChoice` 구조를 먼저 재검증한다. 알려지지 않은 token은 `..._NOT_FOUND`, 이미 처리 중/완료/취소/실패한 token은 `..._ALREADY_CONSUMED`로 고정 거부한다. active record 획득과 terminal `CONSUMED` 전환은 `threading.Lock` 아래 원자적으로 수행하며 downstream 호출 전에 끝낸다. 동시에 두 번 제출하면 최대 한 호출만 provider/compiler/policy/executor에 도달한다.
6. syntactically valid한 첫 submit은 성공·취소·unknown option·provider/compile/policy/executor 실패와 무관하게 영구 소비된다. downstream 예외 뒤 session을 active로 되돌리거나 자동 retry/repair하지 않는다. 이는 중복 read side effect를 막는 at-most-once dispatch이며, 프로세스 crash 후 exactly-once를 주장하지 않는다.
7. 취소는 빈 selection+`cancelled=True`만 허용한다. 저장한 snapshot/pending으로 기존 `resolve_detected_clarification()`을 호출해 context와 choice를 재확인하고 `CANCELLED` 결과를 반환한다. provider/compiler/policy/executor는 호출하지 않는다. selection과 cancel을 함께 주거나 invalid choice면 기존 clarification 오류를 그대로 전파하되 session은 소비된 상태다.
8. 확인 선택은 저장한 snapshot/ruleset/pending과 caller choice만 `run_clarified_execution()`에 전달한다. context/pending은 submit caller에게서 다시 받지 않는다. 기존 adapter가 constraint-aware provider → 내부 one-shot model → validation/compile/policy/read-only execute/result validation을 수행하게 하며 우회 실행은 금지한다. adapter 성공과 compile 이후 실패 `HarnessResult`를 모두 `EXECUTED` submission 결과에 보존한다.
9. versioned frozen `start`/`submit` 결과는 상태별 불변식을 자체 validator로 강제한다. `NOT_REQUIRED`는 token/pending 없음, `PENDING`은 token/pending 있음. `CANCELLED`는 기존 `ClarificationResult`만 있고 execution 없음, `EXECUTED`는 `ClarificationExecutionResult`만 있다. caller가 forged nested 결과를 직접 구성하지 못하도록 dump 재검증과 request fingerprint 일치를 확인한다.
10. session 자체 오류 taxonomy는 `INVALID_INPUT`, `NOT_FOUND`, `EXPIRED`, `ALREADY_CONSUMED`, `INTERNAL_ERROR`의 고정 code만 둔다. 기존 ambiguity/clarification/constraint/adapter 오류는 원문 없이 그대로 전파한다. 모듈은 stdout/log를 쓰지 않는다.
11. active session record는 raw 질문과 context를 프로세스 메모리에 보관한다는 한계를 docstring에 명시한다. submit/expiry 때 active record에서 제거하지만 Python 메모리의 즉시 안전 삭제를 주장하지 않는다. persistence·cross-process 공유·restart 복구·distributed lock·인증/권한·rate limit·HTTP cookie/CSRF·UI label은 범위 밖이다.

## 합성 테스트

- no-match `NOT_REQUIRED`: session/token 없음, provider/executor 호출 없음.
- 단일 match `PENDING`: opaque token과 기존 pending options 반환, caller 입력 변이와 내부 snapshot 격리.
- entity/field 선택 각각 `submit` 1회로 Phase 6-10 실행 성공. provider/compiler/policy/executor 각각 1회, 결과 QueryIR binding 유지.
- cancel은 `CANCELLED`, downstream 0회, 재사용은 `ALREADY_CONSUMED`.
- TTL 경계 직전 성공, deadline에서 `EXPIRED`, provider/executor 0회, 이후에도 재활성화 없음.
- unknown/malformed token, forged choice, unknown/multi/cancel+selection을 고정 오류로 막고 원문 비노출. 첫 syntactically valid invalid-choice submit 뒤 재사용 차단 확인.
- provider/constraint/compiler/policy/executor/result 실패 뒤에도 소비 유지. compile 이후 기존 HarnessResult code/stage 보존.
- 두 thread 동시 submit에서 정확히 하나만 provider/executor에 도달하고 다른 하나는 consumed 오류.
- token factory 충돌이 기존 active/terminal record를 덮지 않음.
- forged start/submit nested result와 public API의 pending/context/IR/result 주입 부재 확인.
- 오류·stdout/log에 token, 질문, raw 식별자, option/rule/trigger, physical mapping, SQL, rows, path, secret, 하위 예외 원문이 없음.
- Phase 6-08/09/10 관련 회귀를 함께 실행한다.

OpenCode에서는 관련 테스트만 실행한다. 전체 pytest, compileall, `git diff --check`, 계획·ADR·handoff 갱신, commit은 Codex 독립 검토 뒤에만 수행한다. 실제 UI/API route가 없으므로 상위 `모호한 질문 감지 및 사용자 확인` 체크는 미완료로 유지한다.
