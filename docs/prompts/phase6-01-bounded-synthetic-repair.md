# Phase 6-01: 합성 입력의 제한된 QueryIR repair 경계

기존 `ExecutionHarness`를 재사용해 **검증 실패한 QueryIR에 한해 최대 한 번** 수정 후보를 받는 작은 coordinator를 구현해줘. `FakeModel` 또는 주입한 합성 repair provider로만 호출·검증한다. 실제 Ollama, 평가 세트 점수, 사용자 화면, 운영 DB는 이번 단계에 연결하지 마. Phase 5의 사람 검토·동결 대기는 그대로 유지한다.

## 먼저 확인

- 시작 시 `git status --short`, 최근 Conventional Commit, 미커밋 변경을 확인하고 보존한다.
- `AGENTS.md`, `docs/schema-adaptive-text-to-sql-plan.md`의 Phase 6·다음 시작점·ADR-022/028·변경 이력, `docs/next-session-handoff.md`, `docs/human-validation-checkpoints.md`의 E2/E3/L1/L3/L5를 읽는다.
- `backend/adaptive/harness/engine.py`, `contracts.py`, `model.py`, `validator.py`, `errors.py`, SQLite compiler·AST policy·read-only executor, `tests/adaptive/test_execution_harness.py`, `test_schema_adaptive_harness.py`를 확인한다.
- 현재 `ExecutionHarness.run()`의 단계별 실패 code와 `HarnessResult`를 먼저 파악한다. legacy `backend/llm.py`는 변경하지 않는다.

## 목표와 계약

1. 기존 `ExecutionHarness` 바깥에 작은 coordinator와 명시적 repair provider 계약을 둔다. 최초 시도는 기존 `QueryModel`과 하네스로 실행한다. repair provider에는 원래 `HarnessRequest`, runtime ontology, `PUBLISHED` mapping, **고정된 실패 code**만 전달한다. gold IR·기대 행·SQL·DB 행·원래 모델 출력·예외 원문·전체 trace는 전달하지 않는다. provider가 반환한 후보도 기존 `QueryIR` validator, compiler, AST policy, read-only executor를 모두 다시 통과해야 한다. SQL 직접 수정·실행 경로는 만들지 않는다.
2. 최대 시도 수는 최초 1회 + repair 1회로 고정한다. 최초 성공이면 repair 호출 0회. `MODEL_INVALID_OUTPUT`은 `IR_GENERATED` 단계에서만, `UNKNOWN_ENTITY`, `UNKNOWN_FIELD`, `INVALID_OPERATOR`, `INVALID_VALUE_TYPE`은 `IR_VALIDATED` 단계에서만 repair 1회를 허용한다. 오류 **code와 stage를 함께** 검사한다. `INVALID_REQUEST`, mapping/context/JOIN/compiler/SQL policy/실행/결과 실패, provider 예외·취소는 terminal이다. repair 실패 뒤 추가 retry·fallback은 없다. 빈 결과 0행은 성공으로 취급하고 repair하지 않는다.
3. 동일 request ID·database ID·질문과 동일 ontology·mapping version을 두 시도에서 사용한다. repair 후보가 요청이나 context를 바꾸지 못하게 한다. 기존 `QueryModel.generate_ir()`와 `ExecutionHarness.run()`의 공개 계약은 유지한다. 생성한 임시 모델 wrapper가 있다면 호출당 후보 1개만 반환한다.
4. 결과는 최초/최종 시도의 상태, stage, 고정 failure code, repair 시도 여부·횟수를 구분해 확인할 수 있게 한다. 기존 `HarnessResult`의 SQL·행·원문 메시지를 새 요약에 복사하지 않는다. 성공은 최종 시도 성공일 때만 성공이다. repair로 성공한 결과를 최초 모델의 정확도나 평가 점수로 세지 않는다. 기본 stdout·로그·통제 오류에는 질문, 원 응답, SQL, 행, 비밀값, 로컬 경로를 넣지 않는다.
5. 실제 provider 호출용 CLI, prompt, 자동 prompt 수정, 임의 예외를 prompt에 이어 붙이는 동작은 구현하지 않는다. provider 예외가 나도 원문을 노출하거나 무한 재시도하지 않도록 고정 code 또는 기존의 안전한 실패 경계로 처리한다. 실제 cancellation은 취소로 전파하고 성공·repair 성공으로 위장하지 않는다.

## 최소 합성 테스트

- 첫 시도 성공, 0행 성공: repair 0회·하네스 1회.
- 구조 오류 및 각 허용 semantic 오류: repair 정확히 1회, 성공 후보는 기존 하네스 전체 안전 경로를 다시 통과해 최종 성공. 최초 실패와 최종 성공을 구분한다.
- repair 후보도 잘못되면 최종 실패, 총 2회에서 중지. provider 자체 실패·취소는 재시도하지 않고 안전하게 종료 또는 전파한다.
- 요청·mapping 오류, 모호한 JOIN, unsafe SQL, 실행 timeout·오류, 결과 schema 오류는 repair 0회. **오류 code가 우연히 같아도 stage가 다르면** 재시도하지 않는다.
- 원본 request/context가 변형되지 않음. repair provider에는 고정 code만 전달되고 gold·원 출력·SQL·행·trace가 전달되지 않음. 원문·경로·비밀값이 새 요약·stdout·기본 로그·통제 오류에 없음.
- 안전성 회귀: invalid IR은 SQL 실행 0회, repair 결과도 AST policy와 read-only executor를 우회하지 않음, DB 변경 0건. 실제 모델·네트워크 호출 0회.
- 결과 횟수·순서와 테스트 분모를 명시한다. `FakeModel` oracle을 쓴 결과는 모델 성능으로 표시하지 않는다.

## 범위와 문서

- Phase 6의 전체 `validate → execute → critique → repair` 로드맵을 이 작은 coordinator만으로 완료 처리하지 않는다. 이번 구현 범위와 남은 critique·사용자 확인·관리자 검토 큐를 분리해 기록한다.
- `docs/schema-adaptive-text-to-sql-plan.md`의 Phase 6 본문·실제 완료 범위의 체크박스·다음 시작점·날짜별 변경 이력을 갱신한다. 재시도 정책이 중요한 결정이면 ADR을 추가한다. `docs/next-session-handoff.md`도 갱신한다.
- 새 30건과 기존 40건의 `NEEDS_HUMAN_REVIEW`를 유지한다. 사람 결정은 지금 요청하지 않는다. 실제 CNU 자료 추가 사용·공유 전 E3, 실제 데이터 배포·외부 시연 전 L5, 최종 모델 성능 주장 전 L1/L3를 적용한다.
- 독립 평가 세트 실행, Ollama/live capture, private recording, `run-recorded`, prompt 탐색, PostgreSQL, FastAPI/React, legacy 시스템 변경은 제외한다.

## 검증·완료 보고

- 새 coordinator의 합성 테스트와 관련 하네스 회귀 테스트를 먼저 실행한다. 마지막에 `.venv/Scripts/python.exe -m pytest tests -q`, `.venv/Scripts/python.exe -m compileall -q backend tests`, `git diff --check`를 실행한다. Windows 임시폴더 `PermissionError`가 나면 우회 환경과 실패·재실행 결과를 구분해 기록한다.
- 변경 파일, 재시도 허용 code/stage, 호출 횟수·안전성 테스트 결과, 기존 하네스 회귀, 미구현 범위와 위험을 보고한다. 성능·정확도 수치를 만들지 않는다. 새 코드가 요청·context를 오염시키거나 안전 검증을 우회하는 등 품질 저하 징후가 있으면 숨기지 말고 구체적 증거와 함께 보고한다.
- 과거 형식의 Conventional Commit으로 커밋하되 push는 하지 않는다.
