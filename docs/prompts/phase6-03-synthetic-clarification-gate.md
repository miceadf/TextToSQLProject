# Phase 6-03: 합성 모호성 신호·사용자 확인 경계

명시적인 모호성 신호가 있을 때 실행을 보류하고, 사용자가 제시된 선택지 하나를 확인했는지 검증하는 **작은 합성 계약**을 구현해줘. 이번 단계는 자동 모호성 감지나 사용자 화면이 아니다. 기존 `INVALID_REQUEST`, 0행, 모델 오류, `CritiqueDisposition`에서 모호성을 추측하지 않는다.

## 먼저 확인

- 시작 시 `git status --short`, 최근 Conventional Commit을 확인하고 기존 미커밋 변경을 보존한다.
- `AGENTS.md`, `docs/schema-adaptive-text-to-sql-plan.md`의 Phase 6·ADR-029/030·다음 시작점, `docs/next-session-handoff.md`, `docs/human-validation-checkpoints.md`의 E2/E3/L1/L3/L5를 읽는다.
- `backend/adaptive/harness/contracts.py`, `repair.py`, `critique.py`, `engine.py`, `__init__.py`와 해당 합성 테스트를 읽고 실제 호출 순서를 확인한다.

## 범위와 계약

1. 별도 `clarification` 모듈에 versioned, 명시적 `AmbiguitySignal`과 `PendingClarification`, 사용자 `ClarificationChoice` 및 결정적 결과/고정 오류 code를 둔다. 신호는 caller가 제공하며 질문의 의미를 모델이나 문자열 규칙으로 추론하지 않는다. 후보는 최소 2개이고 서로 다른 안정적인 option ID와 **semantic** 설명 식별자를 갖는다. 물리 table/column, SQL, 결과 행, 자유 형식 사용자 답변은 후보에 넣지 않는다. 선택지의 표시 문구가 필요하면 이번 단계에는 출력하지 않고 ID만 다룬다.
2. `prepare`는 유효한 `HarnessRequest`, runtime `Ontology`, `PUBLISHED MappingProfile`, 명시적 신호를 받아 pending 상태를 만든다. 신호가 없으면 pending을 만들거나 호출을 막지 않는다. request ID, 질문 fingerprint, ontology/mapping의 결정적 fingerprint, 허용 option ID를 pending에 묶어 둔다. pending·결과·예외·stdout·기본 로그에 질문 원문, physical mapping, SQL, 행, 경로, secret을 넣지 않는다. hash는 canonical UTF-8 JSON에서 계산하고 원문을 저장하지 않는다.
3. `resolve`는 원래 request·ontology·mapping과 pending, 선택을 재검증한다. request ID·질문·ontology·mapping 중 하나라도 달라지거나, option ID가 없거나 중복·변조되면 고정 오류로 fail-closed한다. 정확히 한 개의 제시된 option ID를 선택한 경우에만 `CONFIRMED`와 선택 ID를 반환한다. `CANCELLED`는 별도 고정 상태로 반환하고 임의의 기본 선택지를 고르지 않는다. 임의 free-text 답변, 빈 답변, 여러 선택은 자동 확인으로 인정하지 않는다. 호출자가 재사용 가능한 stateless 순수 계약이라면 한 번만 쓰는 보증을 주장하지 말고 한계를 문서화한다.
4. 질문 원문을 바꾸거나 QueryIR을 보정·생성하지 않는다. 확인 결과만으로 `ExecutionHarness`나 `BoundedRepairCoordinator`를 자동 실행하지 않는다. 실제 UI/API, 관리자 큐, 저장소, DB, 네트워크, Ollama, legacy 경로를 변경하지 않는다. 기존 `CritiqueResult`와 오류 taxonomy도 변경하지 않는다. `INVALID_REQUEST` 등 기존 실패를 이 경계로 자동 승격시키지 않는다.
5. 입력 모델의 Pydantic 오류와 통제 오류의 문자열에 사용자 입력이 새지 않도록 공개 함수의 경계를 설계한다. 기존 타입의 raw `ValidationError`를 그대로 노출하는 패턴을 피하고 고정 code만 외부에 전달한다.

## 최소 합성 테스트

- 명시적 신호가 없을 때 pending·모델·DB 호출 0회. 신호가 있을 때 2개 이상 option과 published mapping에서 pending 생성, 정확한 한 선택 확인 및 취소 분리.
- 질문, request ID, ontology, mapping 변경마다 이전 pending 거부. 동일 입력은 같은 fingerprint, 순서가 의미 없는 set/dict 순서 변경은 동일 fingerprint.
- 후보 0/1개, 중복 ID, 존재하지 않는 semantic field/entity, 미발행 mapping, 물리 이름 주입, 미지 option, 다중·빈·자유 텍스트 답변을 거부.
- `INVALID_REQUEST`, 0행, model invalid output, `MODEL_REVIEW`만으로 신호가 자동 생성되지 않음.
- 원문 질문·물리 이름·임의 악성 문자열·로컬 경로가 pending·결과·예외·stdout·기본 로그에 없음. model/provider/executor/DB/network 호출 0회.
- 관련 테스트 먼저, 마지막에 `.venv/Scripts/python.exe -m pytest tests -q`, `.venv/Scripts/python.exe -m compileall -q backend tests`, `git diff --check`. Windows 임시폴더 ACL 우회가 필요하면 원 명령 실패와 우회 실행을 구분해 보고한다.

## 문서와 완료

- 실제 완료 범위만 plan Phase 6 본문·체크박스·다음 시작점·변경 이력에 반영하고, 중요한 계약 결정은 ADR로 남긴다. `docs/next-session-handoff.md`를 갱신한다. `모호한 질문 감지 및 사용자 확인` 전체 항목은 자동 감지와 실제 상호작용이 없으므로 완료 표시하지 않는다.
- E3/L5·L1/L3의 팀 결정을 지금 요청하지 않는다. 40건·30건 `NEEDS_HUMAN_REVIEW` 유지. 실제 CNU 자료와 private bundle을 사용하지 않는다.
- 변경 파일, 검증 결과, 남은 한계, 코드 품질 징후를 보고한다. Conventional Commit으로 커밋하되 push는 하지 않는다.
