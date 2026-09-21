# Phase 6-09: 확인 선택의 QueryIR constraint 전달

`ambiguity-detector-v1`과 `clarification-gate-v2`가 확인한 선택을 후속 QueryIR 생성에 명시적으로 전달하고, 생성 결과가 그 선택을 지켰는지 실행 전에 검증하는 합성 경계를 구현한다. **재감지·재확인 → semantic constraint 생성 → constraint-aware provider 1회 호출 → QueryIR·constraint 검증**을 한 기능 흐름으로 닫는다. 기존 `QueryModel`·`ExecutionHarness`·repair loop, 실제 UI·DB·Ollama·legacy는 변경하지 않는다.

## 먼저 확인

- 시작 시 `git status --short`와 최근 commit을 확인하고 기존 변경을 보존한다.
- `AGENTS.md`, `docs/next-session-handoff.md`, 계획 문서 Phase 6·다음 시작점·ADR-031/036, `docs/human-validation-checkpoints.md`의 E3/L1/L3/L5를 따른다.
- `clarification.py`, `ambiguity_detector.py`, `contracts.py`, `validator.py`, `model.py`와 해당 합성 테스트만 읽는다.

## 구현 계약

1. 별도 작은 모듈에 `clarification-query-constraint-v1` 계약을 둔다. 기존 `QueryModel.generate_ir(request, ontology, mapping)` protocol과 `ExecutionHarness`는 변경하지 않는다. 새 합성 provider protocol만 `generate_ir(request, ontology, mapping, constraint)`를 받는다.
2. 공개 coordinator는 request, runtime ontology, Published mapping, detector ruleset, pending, 원래 `ClarificationChoice`, constraint-aware provider를 입력받는다. caller가 `ClarificationResult`, `AmbiguitySignal`, semantic_ref, constraint를 직접 주입하는 API는 두지 않는다.
3. 모델 호출 전에 기존 `resolve_detected_clarification()`을 호출해 동일 context/ruleset에서 선택을 다시 확인한다. 같은 규칙으로 signal을 재생성하고, 확인 결과의 opaque selected fingerprint와 정확히 일치하는 option의 ontology entity/field `semantic_ref`만 constraint로 승격한다. 취소·no/multi match·pending/choice/context/ruleset drift·위조 입력은 모델 호출 전에 fail closed한다. 기존 ambiguity/clarification 고정 오류는 그대로 보존한다.
4. constraint는 frozen strict Pydantic 계약으로 version, request/database domain fingerprint, ontology/mapping fingerprint와 선택된 semantic_ref만 가진다. raw request/database/option/rule/trigger, 질문, 물리 table/column, SQL, 행, path, secret, display/free text를 저장하지 않는다. 모든 공개 입력과 nested 반환은 warning 비노출 plain JSON dump 후 재검증한다.
5. provider에는 재검증한 request/ontology/mapping/constraint의 방어적 깊은 복사만 전달한다. 정확히 1회 호출하고 provider 변이가 caller 객체나 사후 검증 context에 영향을 주지 못하게 한다. provider 예외는 원문을 반영하지 않는 새 고정 오류로 변환한다.
6. provider 반환은 `QueryIR`로 strict하게 검증한 뒤 기존 `validate_query_ir()`로 ontology/mapping 의미 검증을 통과해야 한다. model output/semantic validation 실패는 원문·값을 포함하지 않는 새 고정 오류로 닫고 기존 harness error/result를 위조하지 않는다.
7. 선택 constraint 준수 규칙은 작고 명시적으로 고정한다. 선택이 entity ref이면 `QueryIR.entity`가 정확히 같아야 한다. field ref이면 해당 ref가 `select` 또는 `filters[].field`에 최소 한 번 있어야 한다. 불일치는 고정 constraint violation이며 실행·compile·repair로 넘기지 않는다. 이는 질문 의미의 완전한 정답 판정이나 만족도 증거가 아니다.
8. 성공 반환은 versioned frozen 결과로 검증된 constraint와 QueryIR만 가진다. provider 호출 횟수나 자유 설명은 반환하지 않는다. 파일·DB·network·executor·compiler·repair·queue·snapshot을 호출하지 않는다.
9. 새 error code는 invalid input/context, cancelled, provider failure, invalid QueryIR, constraint violation처럼 고정 taxonomy로 제한한다. 입력·provider 예외 문자열을 message/log/stdout에 반영하지 않는다. 기존 ambiguity/clarification 오류 taxonomy는 감싸지 말고 유지한다.
10. package 공개 API를 추가하고 모듈 docstring에 신뢰 경계, entity/field 준수 규칙, bearer pending·규칙 기반 탐지 한계, 실행/UI/session/expiry 부재를 기록한다.

## 합성 테스트

- entity 선택과 field 선택이 constraint로 전달되고 준수 QueryIR가 성공한다.
- field는 select 또는 filter 사용을 각각 허용하며 미사용, 다른 entity는 constraint violation이다.
- cancel, no/multi match, request/ontology/mapping/ruleset/pending/choice drift·위조는 provider 0회다.
- provider 1회, 입력 방어적 복사, provider 변이 격리, provider 예외 고정 오류를 검증한다.
- invalid QueryIR와 기존 validator 실패를 고정 오류로 막고 compiler/executor/repair 호출 0회를 검증한다.
- constraint/result/error/stdout/log에 raw 질문·식별자·option/rule/trigger·물리 mapping·SQL·행·path·secret이 없는지 검사한다.
- Pydantic `model_copy(update=...)`·`model_construct` 위조와 serializer warning 비노출을 검사한다.
- 기존 ambiguity detector·clarification gate 회귀를 함께 실행한다.

OpenCode에서는 관련 테스트만 실행한다. 전체 pytest, compileall, `git diff --check`, 계획·ADR·handoff 갱신, commit은 Codex 독립 검토 뒤에만 수행한다. 상위 `모호한 질문 감지 및 사용자 확인` 체크박스는 실제 UI·실행 경로 통합 전까지 미완료로 유지한다.
