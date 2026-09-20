# Phase 6-02: 합성 결과의 결정적 critique 분류

Phase 6-01의 `BoundedRepairCoordinator`가 반환하는 `RepairSummary`를 **고정된 후속 처리 분류**로 바꾸는 작은 순수 함수를 구현해줘. 이번 critique는 모델이 답을 맞혔는지 판정하지 않는다. 성공은 안전한 실행이 끝났다는 뜻만 갖고, 실패는 code·stage에 따라 후속 검토 대상을 분류한다. 합성 입력과 테스트만 사용한다.

## 먼저 확인

- `git status --short`, 최근 Conventional Commit, 미커밋 변경을 확인하고 보존한다.
- `AGENTS.md`, `docs/schema-adaptive-text-to-sql-plan.md`의 Phase 6·ADR-029·다음 시작점·변경 이력, `docs/next-session-handoff.md`, `docs/human-validation-checkpoints.md`의 E2/E3/L1/L3/L5를 읽는다.
- `backend/adaptive/harness/repair.py`, `contracts.py`, `engine.py`, `errors.py`, `__init__.py`, `tests/adaptive/test_bounded_repair.py`와 관련 하네스 테스트를 확인한다.
- 기존 실행 오류 taxonomy와 단계별 실제 발생 위치를 확인한다. 문서의 예정 기능과 현재 코드의 완료 상태를 혼동하지 않는다.

## 구현 범위

1. `RepairSummary`만 받는 결정적 critique 함수와 작은 결과 계약을 추가한다. 결과에는 version 식별자, 최종 disposition, 고정된 failure code·stage, 최초 성공인지 repair 후 성공인지 정도만 넣는다. `HarnessResult`, 질문, QueryIR, SQL, 결과 행, 원문 예외·메시지, trace, 경로, 비밀값을 입력·출력·로그에 포함하지 않는다. 파일·DB·네트워크 접근도 하지 않는다.
2. 성공한 최초 실행과 repair 후 성공은 둘 다 `EXECUTED_UNVERIFIED`로 분류하되 repair 사용 여부는 구분한다. 0행 성공도 같다. **정답, 사용자 만족, 모델 정확도, 안전한 답변 내용**을 추론하지 않는다.
3. 실패는 최종 code와 stage의 허용 조합을 명시적으로 분류한다. 최소 범주는 `REQUEST_INVALID`(입력 계약), `MODEL_REVIEW`(IR 구조·semantic 오류 및 repair provider 실패), `CONTEXT_REVIEW`(DB·Published Mapping·JOIN/관계 문제), `SAFETY_BLOCKED`(SQL policy 거부), `EXECUTION_FAILURE`(timeout·실행·결과 검증), `INTERNAL_REVIEW`(컴파일 등 내부 결함 또는 분류 불가)다. `REPAIR_PROVIDER_ERROR`의 stage 없음은 명시적 경우로 다룬다. 실제 `HarnessErrorCode` 전체와 새 provider code를 살펴 빠진 code가 조용히 성공·사용자 질문으로 바뀌지 않게 한다. code만 맞고 stage가 어긋나는 합성 입력, 알 수 없는 code, 서로 모순된 status·code·repair 횟수는 고정 오류 code로 거부하거나 `INTERNAL_REVIEW`로 닫는다. 오류 원문은 반영하지 않는다.
4. 자동 사용자 확인 요청이나 관리자 queue 등록은 하지 않는다. 분류 결과는 **후속 작업 제안**일 뿐 승인·publish·repair 재시도 지시가 아니다. 모호한 질문은 현재 구조화된 신호가 없으므로 `INVALID_REQUEST`, 0행, 모델 오류만 보고 모호하다고 추측하지 않는다. 별도 ambiguity 신호와 사용자 확인 계약은 다음 단계로 남긴다.
5. Phase 6-01의 재시도 목록·최대 1회·context 격리, `ExecutionHarness`, `QueryModel`, legacy 경로를 변경하지 않는다. 결과가 모델 성능 report로 흘러가는 새 경로를 만들지 않는다.

## 최소 합성 테스트

- 실제 `BoundedRepairCoordinator` 결과로 최초 성공, repair 후 성공, 최초 실패, repair 실패, `REPAIR_PROVIDER_ERROR`를 분류한다. 성공 두 종류 모두 `EXECUTED_UNVERIFIED`이며 repair 여부는 다르게 남는다.
- 성공 0행은 성공 분류. `INVALID_REQUEST`는 사용자 **질문 모호성**으로 취급하지 않는다.
- mapping/JOIN, SQL policy 차단, 실행 timeout·오류·결과 schema 오류, 내부 컴파일 오류의 대표 code·stage를 분류한다. safety 차단과 시스템 실패가 model failure로 섞이지 않는다.
- 존재하는 모든 `HarnessErrorCode`와 `REPAIR_PROVIDER_ERROR`의 처리 범위를 테스트로 고정한다. 새 code가 생기면 정책 검토 없이 조용히 기본 성공 처리되지 않는다.
- 위조된 stage, 알 수 없는 code, 모순된 `RepairSummary`는 fail-closed이며 고정 오류만 보인다. 요약·예외·stdout·기본 로그에 질문·SQL·결과 행·로컬 경로·임의 오류 원문이 없다.
- 분류는 순수 함수이고 model/provider/executor/DB/네트워크 호출 0회다. 기존 repair·하네스 관련 테스트도 유지한다.

## 문서·검증·보고

- `docs/schema-adaptive-text-to-sql-plan.md`의 Phase 6 본문·실제 완료 범위의 체크박스·다음 시작점·변경 이력을 갱신하고, 분류 정책을 고정하는 ADR을 추가한다. `docs/next-session-handoff.md`도 짧게 갱신한다. 전체 `validate → execute → critique → repair` loop, 모호한 질문 확인, 관리자 검토 큐는 아직 완료로 표시하지 않는다.
- 관련 합성 테스트를 먼저 실행하고 마지막에 `.venv/Scripts/python.exe -m pytest tests -q`, `.venv/Scripts/python.exe -m compileall -q backend tests`, `git diff --check`를 실행한다. Windows 임시폴더 ACL 실패는 실패한 명령과 재실행 결과를 구분해 보고한다. 임시 경로를 만들면 사용 후 정리하고, 재귀 삭제 전 절대경로가 저장소 내부인지 확인한다.
- E3/L5·L1/L3 팀 결정을 지금 요청하지 않는다. 기존 40건과 독립 30건의 `NEEDS_HUMAN_REVIEW`를 유지한다. 실제 데이터·private bundle·Ollama·PostgreSQL·FastAPI/React·prompt 탐색·평가 점수는 범위 밖이다.
- 변경 파일, code별 분류 계약, 테스트, 미구현 범위, 위험을 보고한다. 코드 품질 저하나 요구사항 누락 징후도 숨기지 않는다. 최근 형식의 Conventional Commit으로 커밋하되 push는 하지 않는다.
