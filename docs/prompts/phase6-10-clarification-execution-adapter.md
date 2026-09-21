# Phase 6-10: 확인된 QueryIR의 안전 실행 adapter

Phase 6-09의 constraint 검증을 통과한 QueryIR를 기존 `ExecutionHarness`의 **request/context 검증 → QueryIR 재검증 → compile → SQL policy → read-only execute → result 검증** 경로에 우회 없이 전달하는 합성 adapter를 구현한다. 확인·생성·실행을 한 명시적 호출로 닫되 기존 `ExecutionHarness`, `QueryModel`, compiler, policy, executor 계약은 변경하지 않는다. 실제 UI·PostgreSQL·Ollama·CNU 자료·repair는 제외한다.

## 먼저 확인

- 시작 시 `git status --short`와 최근 commit을 확인하고 기존 변경을 보존한다.
- `AGENTS.md`, `docs/next-session-handoff.md`, 계획 문서 Phase 6·다음 시작점·ADR-031/036/037, `docs/human-validation-checkpoints.md`의 E3/L1/L3/L5를 따른다.
- `clarification_query_constraint.py`, `engine.py`, `compiler.py`, `policy.py`, `executor.py`, `contracts.py`와 관련 합성 테스트만 읽는다. legacy FastAPI/React와 기존 Ollama/PostgreSQL 경로는 변경하지 않는다.

## 구현 계약

1. 별도 작은 모듈에 `clarification-execution-adapter-v1`을 둔다. 공개 coordinator는 원래 request, runtime ontology, Published mapping, detector ruleset, pending, `ClarificationChoice`, constraint-aware provider, read-only executor를 받는다. caller가 `QueryIR`, `ConstrainedGenerationResult`, constraint, `QueryModel`, `HarnessResult`를 직접 주입하는 API는 두지 않는다.
2. provider/executor/compiler/policy의 필수 callable과 모든 context를 provider 호출 전에 검사한다. 공개 Pydantic 입력은 warning 비노출 plain JSON dump 후 재검증하고 깊은 snapshot을 만든다. invalid dependency/context, cancel, no/multi match, pending/choice/ruleset/context drift는 provider·compiler·policy·executor 호출 전에 fail closed한다. 기존 ambiguity/clarification/constraint 오류는 감싸지 않고 보존한다.
3. snapshot만 Phase 6-09 `run_constrained_generation()`에 전달한다. provider는 정확히 1회 호출되며 성공하면 검증된 `ConstrainedGenerationResult`를 dump-then-validate로 다시 고정한다. provider가 caller나 실행 snapshot을 변이하지 못하게 한다.
4. adapter 내부 전용 one-shot `QueryModel`만 만든다. 이 모델은 외부 주입할 수 없고 Phase 6-09의 검증된 QueryIR 한 개를 가진다. `ExecutionHarness`가 전달한 request/ontology/mapping이 adapter snapshot과 완전히 같을 때만 QueryIR의 방어적 복사본을 정확히 1회 반환한다. 두 번째 호출이나 context drift는 원문 없는 고정 내부 오류로 닫는다.
5. 기존 `ExecutionHarness`를 그대로 생성해 snapshot request/ontology/mapping으로 정확히 1회 `run()`한다. 기본 compiler는 기존 `SingleTableSQLiteCompiler`, 기본 policy는 기존 `ConservativeSqlPolicy`를 사용한다. 합성 테스트를 위해 기존 protocol과 호환되는 compiler/policy 주입은 허용하되 prebuilt SQL/result를 받지 않는다. read-only executor는 주입받지만 adapter가 `execute()`를 직접 호출하지 않는다.
6. 성공과 compile/policy/execution/result 실패는 기존 `HarnessResult`·`HarnessErrorCode`·stage를 그대로 보존한다. 자동 repair, 재생성, 재확인 반복, 성공 추정, critique/queue 등록을 하지 않는다. constraint/provider 단계 실패에는 executor가 호출되지 않는다.
7. versioned frozen 결과는 `adapter_version`, 재검증된 `ConstrainedGenerationResult`, 재검증된 `HarnessResult`만 가진다. harness의 `query_ir`는 generation의 QueryIR와 정확히 같아야 한다. 이미 생성·검증된 QueryIR이므로 harness 결과가 request/context/IR-generated/IR-validated 단계에서 실패하거나 query_ir가 없거나 달라지면 고정 adapter 내부 오류로 fail closed한다. compile 이후의 정상 harness 실패는 결과로 반환한다.
8. 결과의 `HarnessResult`는 기존 계약상 raw request ID, compiled SQL, columns/rows 또는 고정 오류 message를 포함할 수 있다. adapter가 이를 숨겼다고 주장하지 않는다. 새 adapter 오류·로그·stdout에는 질문, raw 식별자, option/rule/trigger, 물리 mapping, SQL, 행, path, secret, provider/OS 예외 문자열을 반영하지 않는다. 결과 보관·표시·접근 통제는 후속 API/UI 경계다.
9. adapter 자체 오류는 `EXECUTION_ADAPTER_INVALID_INPUT`과 `EXECUTION_ADAPTER_INTERNAL_ERROR`처럼 최소 고정 code만 둔다. 기존 하위 경계 오류는 그대로 전파한다. provider·compiler·policy·executor 호출 횟수를 결과에 넣지 않는다.
10. 파일·network·Ollama·PostgreSQL·queue·snapshot을 adapter가 직접 접근하지 않는다. 테스트용 기존 SQLite read-only executor 호출은 명시적 주입을 통해서만 발생한다. 모듈 docstring에 신뢰 경계, one-shot 모델, 기존 harness 재사용, HarnessResult의 raw content, 실행 후 side effect와 UI/session/expiry 부재를 기록한다.

## 합성 테스트

- entity/field 선택 각각에서 provider 1회 → 내부 model 1회 → compiler/policy/executor 1회로 성공하고 결과 QueryIR가 generation과 같다.
- 기존 `SQLiteReadOnlyExecutor`와 임시 합성 SQLite fixture를 사용해 실제 read-only 성공·0행 성공을 검증한다.
- cancel, no/multi match, request/ontology/mapping/ruleset/pending/choice 위조·drift, invalid dependency는 모든 downstream 호출 0회다.
- constraint 위반·provider 오류·invalid QueryIR는 compiler/policy/executor 0회다.
- compiler, policy, executor, result-schema 실패는 기존 code/stage와 검증된 QueryIR를 보존하고 재생성·repair하지 않는다.
- 악성 compiler SQL이 policy를 우회하지 못하고 executor 0회인지 검사한다.
- provider mutation과 executor가 받은 compiled query 변이가 caller/context/generation 결과를 바꾸지 않는지 검사한다.
- 내부 one-shot 모델의 두 번째 호출·context drift와 위조 nested 결과를 고정 내부 오류로 막는다. 공개 API로 모델/IR/result 주입이 불가능한지도 확인한다.
- adapter 오류·stdout/log에 raw 질문·식별자·option/rule/trigger·물리 mapping·SQL·행·path·secret·예외 원문이 없는지 검사한다. 기존 성공 `HarnessResult`가 SQL/rows를 포함한다는 경계는 명시적으로 확인한다.
- 기존 Phase 6-08/09 clarification·constraint 회귀를 함께 실행한다.

OpenCode에서는 관련 테스트만 실행한다. 전체 pytest, compileall, `git diff --check`, 계획·ADR·handoff 갱신, commit은 Codex 독립 검토 뒤에만 수행한다. 상위 `모호한 질문 감지 및 사용자 확인`은 실제 API/UI·session/expiry 전까지 미완료로 유지한다.
