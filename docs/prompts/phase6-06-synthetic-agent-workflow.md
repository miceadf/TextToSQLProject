# Phase 6-06: 합성 agent workflow 통합

기존 `BoundedRepairCoordinator`, `critique_repair_summary()`, `InMemoryReviewQueue`를 하나의 합성 실행 흐름으로 연결해줘. 이번 단계에서는 최종 `HarnessResult`를 잃지 않으면서 **validate → execute → bounded repair → critique → failure queue registration**을 한 번의 명시적 coordinator 호출로 수행한다. 실제 Ollama·PostgreSQL·CNU 자료·UI·운영 영속 큐는 사용하지 않는다.

## 먼저 확인

- 시작 시 `git status --short`, 최근 Conventional Commit을 확인하고 기존 변경을 보존한다.
- `AGENTS.md`, `docs/schema-adaptive-text-to-sql-plan.md`의 Phase 6·ADR-029~033·다음 시작점, `docs/next-session-handoff.md`, `docs/human-validation-checkpoints.md`의 E2/E3/L1/L3/L5를 읽는다.
- `backend/adaptive/harness/repair.py`, `critique.py`, `review_queue.py`, `review_queue_storage.py`, `engine.py`, `contracts.py`, 해당 테스트를 읽는다. legacy `backend/llm.py`와 기존 FastAPI/React 경로는 변경하지 않는다.

## 구현 계약

1. `BoundedRepairCoordinator`에 상세 결과 계약을 추가한다. 최초 실행 또는 repair 실행에서 얻은 **최종 `HarnessResult`와 기존 `RepairSummary`를 함께** 반환하는 immutable model이어야 한다. 기존 `run()`의 반환형·동작은 보존하고 새 상세 실행 API에 위임하게 한다. 모델·repair provider·executor 호출 횟수를 늘리거나 같은 요청을 재실행해서 결과를 복원하지 않는다.
2. terminal initial failure와 repair 후 성공/실패는 해당 최종 harness result를 보존한다. repair provider 자체가 예외를 내 `REPAIR_PROVIDER_ERROR`가 된 경우에는 그 code를 가짜 `HarnessErrorCode`나 가짜 harness result로 만들지 말고, 상세 계약에서 harness result 부재를 명시적으로 표현한다. 취소 `BaseException` 전파, 최대 repair 1회, 전체 안전 경로 재통과, context deep-copy 격리 계약은 유지한다.
3. 별도 작은 `AgentWorkflowCoordinator`를 추가한다. 명시적 `model_run_id`, request, ontology, Published Mapping, model, repair provider, 주입된 `InMemoryReviewQueue`를 받아 상세 repair 실행 → `critique_repair_summary()` → 운영 실패 후보 queue 등록을 순서대로 수행한다. 성공과 `REQUEST_INVALID`은 기존 큐 계약대로 저장하지 않는다. caller가 `CritiqueResult`나 disposition을 주입할 수 없어야 한다.
4. workflow 반환 계약에는 version, 최종 `RepairSummary`, `CritiqueResult`, queue registration 상태/항목, 가능한 경우 최종 `HarnessResult`를 포함한다. 성공 행과 0행 성공도 보존하되 정확도·정답·사용자 만족을 추론하지 않는다. 실패의 raw exception, 질문, SQL, 행, 물리 mapping, local path, secret을 새 요약·queue·오류·stdout·기본 로그에 복사하지 않는다. 기존 `HarnessResult` 자체의 SQL/rows 계약은 바꾸지 않는다.
5. 실행 전에 가능한 workflow 입력과 queue identity를 검증해 잘못된 `model_run_id`, valid request의 queue-incompatible request ID, 잘못된 queue/type 때문에 모델·repair·DB 실행 후 실패하지 않게 한다. queue의 ID 규칙은 하나의 코드 출처를 공개 helper로 재사용하고 복제하지 않는다. invalid `HarnessRequest`는 기존 harness/critique의 `REQUEST_INVALID` 결과를 유지하며 queue에 넣지 않는다. 입력 객체와 반환 객체의 사후 mutation이 coordinator/queue 내부 상태를 바꾸지 않게 한다.
6. queue conflict나 예상 밖 내부 계약 불일치는 고정 code로 fail closed하고 기존 queue entry를 보존한다. 실행 완료 뒤 queue 등록이 실패할 수 있는 남은 경계와 side effect를 문서화한다. 자동 snapshot write, overwrite, retry, clarification 생성/해결, QueryIR 보정, mapping/glossary/verified-query 변경은 하지 않는다.
7. 기존 `ExecutionHarness`, `QueryModel`, public `BoundedRepairCoordinator.run()`, critique taxonomy, review queue 분류 및 snapshot schema의 기존 계약을 깨지 않는다.

## 합성 테스트

- 최초 성공, 0행 성공: 모델 1회, repair 0회, `EXECUTED_UNVERIFIED`, queue 미등록, 최종 columns/rows 유지.
- retryable 초기 실패 후 repair 성공: 모델 1회 + repair provider 1회 + repair candidate 실행 1회, recovered flag와 최종 결과 유지, queue 미등록.
- retryable 실패 후 repair 후보도 실패: 최종 code/stage 기준 critique 후 queue 1건 등록. 같은 workflow 재등록은 `ALREADY_PRESENT`, 다른 결과의 같은 run/request는 conflict이며 기존 entry 보존.
- terminal validation/context/policy/execution 실패: repair 없음, 기존 disposition에 맞는 운영 실패만 queue 등록. `INVALID_REQUEST`는 미등록.
- repair provider 예외: `REPAIR_PROVIDER_ERROR`, harness result 없음, `MODEL_REVIEW` queue 등록. 실제 cancellation은 전파하고 queue 변화 없음.
- invalid model run ID·queue-incompatible valid request ID·잘못된 queue/type는 모델/provider/executor 0회, queue 0개. 입력 mutation provider를 써도 caller request/ontology/mapping과 검증 context 불변.
- workflow 반환 모델과 queue entry의 방어적 복사, 실행당 중복 호출 없음, 실패 뒤 별도 다음 요청 처리 가능.
- 새 workflow 계층이 파일·네트워크·Ollama를 호출하지 않으며 snapshot 자동 저장 0회. 질문·원문 응답·SQL·행·secret/path가 새 summary/queue/error/stdout/log에 노출되지 않음. 단, 성공/실패 `HarnessResult`는 기존 계약대로 명시적 호출자 반환값임을 테스트와 문서에서 구분한다.
- 기존 repair·critique·queue·snapshot 테스트 회귀. 관련 테스트를 먼저 실행하고 마지막에 `.venv/Scripts/python.exe -m pytest tests -q`, `.venv/Scripts/python.exe -m compileall -q backend tests`, `git diff --check` 실행. Windows 기본 temp ACL 오류와 접근 가능한 임시 경로 재실행을 구분해 보고한다.

## 문서·완료 기준

- 실제 완료 범위만 plan Phase 6 본문·체크박스·다음 시작점·변경 이력에 반영한다. 이 통합으로 계약상 전체 loop가 완성되었다면 `validate → execute → critique → repair loop 구현`만 완료 처리한다. `모호한 질문 감지 및 사용자 확인`, 실제 운영 영속 큐, 교정 반영은 미완료로 둔다. 중요한 결과 보존·side-effect 결정은 ADR로 남긴다.
- `docs/next-session-handoff.md`를 갱신한다. 40건·30건 `NEEDS_HUMAN_REVIEW`, E3/L5·L1/L3 대기를 유지하고 지금 팀 결정을 요청하지 않는다.
- 실제 Ollama·PostgreSQL·CNU 데이터·private recording bundle·live 모델·성능 측정·FastAPI/React를 건드리지 않는다.
- 변경 파일, 호출 횟수, 테스트, 최종 결과 보존 방식, queue side effect와 남은 위험을 보고한다. Conventional Commit으로 커밋하되 push는 하지 않는다.
