# Phase 6-04: 합성 관리자 검토 큐 계약

Phase 6-01의 `RepairSummary`와 Phase 6-02의 `critique_repair_summary()`를 연결해 **운영 실패 후보만 메모리 내 검토 큐에 등록**하는 작은 합성 경계를 구현해줘. 이번 단계는 실제 운영 저장·관리자 UI·사람 승인 흐름이 아니다. Phase 6-03 `clarification-gate-v2`의 사용자 확인 결과를 관리자 승인으로 해석하지 않는다.

## 먼저 확인

- 시작 시 `git status --short`, 최근 Conventional Commit을 확인하고 기존 미커밋 변경을 보존한다.
- `AGENTS.md`, `docs/schema-adaptive-text-to-sql-plan.md`의 Phase 6·ADR-029/030/031·다음 시작점, `docs/next-session-handoff.md`, `docs/human-validation-checkpoints.md`의 E2/E3/L1/L3/L5를 읽는다.
- `backend/adaptive/harness/repair.py`, `critique.py`, `clarification.py`, `contracts.py`, `__init__.py` 및 관련 합성 테스트를 확인한다. 최신 `019b07d`의 위조 요약 거부와 option ID 비노출 수정을 보존한다.

## 구현 계약

1. 새 작은 `review_queue` 모듈에 `InMemoryReviewQueue` 또는 동등한 주입 가능한 메모리 구현을 둔다. 입력은 원본 `RepairSummary`, 명시적 request ID·model run ID다. **내부에서 기존 `critique_repair_summary()`를 호출**해 분류하며, caller가 임의의 `CritiqueResult`를 넣거나 disposition을 조작하는 API를 만들지 않는다. 기존 coordinator·critique·clarification·harness 동작을 바꾸지 않는다.
2. `EXECUTED_UNVERIFIED`와 `REQUEST_INVALID`는 등록하지 않는다. `MODEL_REVIEW`, `CONTEXT_REVIEW`, `SAFETY_BLOCKED`, `EXECUTION_FAILURE`, `INTERNAL_REVIEW`만 운영 실패 후보로 등록한다. `REPAIR_PROVIDER_ERROR`도 기존 critique 분류에 따른다. 등록 결과는 `ENQUEUED`, 동일 항목의 `ALREADY_PRESENT`, 또는 `NOT_ELIGIBLE`을 명시한다. 이는 관리자에게 보여줄 **검토 후보**일 뿐 승인·publish·수정·재시도 지시가 아니다.
3. 큐 항목은 version, run/request ID의 domain-separated SHA-256, 최종 고정 code·stage·disposition, repair 시도/회복 여부와 안정적인 idempotency key만 가진다. 질문·QueryIR·SQL·행·원문 예외·물리 mapping·path·secret·raw run/request ID는 저장·출력·기본 로그에 넣지 않는다. hash 규칙은 canonical UTF-8 기준으로 문서화하고 테스트로 고정한다. ID 원문에 로컬 경로나 임의 secret 문자열이 들어와도 항목/결과/통제 오류에는 드러나지 않아야 한다.
4. 같은 run/request 조합에 같은 결과를 재등록하면 항목 1개만 유지한다. **같은 키에 다른 결과**가 오면 이전 항목을 덮어쓰지 말고 고정 오류 code로 거부한다. 입력 검증·critique 실패 시 큐는 변하지 않는다. 항목 반환과 목록 조회는 외부 변이가 내부 상태를 바꿀 수 없도록 방어적으로 처리한다. 순서는 첫 등록 순서다.
5. 이 모듈은 메모리 전용이며 disk/DB/network/Ollama/private bundle 접근 0회다. 영속성·동시성·여러 프로세스 간 dedupe·관리자 작업 상태를 보장하지 않는다. 실제 운영 큐 저장 체크박스와 전체 `validate → execute → critique → repair` loop 체크박스는 완료하지 않는다.

## 최소 합성 테스트

- 실제 `BoundedRepairCoordinator`의 성공, 사용자 입력 실패, model IR 오류·repair provider 실패, context·policy·execution·internal 실패의 대표 요약을 큐 정책에 통과시킨다. 성공·입력 실패는 항목 0개, 운영 실패는 고정 메타데이터만 1개.
- 동일 항목 재등록은 `ALREADY_PRESENT`, 같은 run/request의 다른 결과는 고정 conflict code이며 기존 항목 불변. 다른 run/request는 순서대로 별개 항목.
- 위조 `RepairSummary`의 2회 repair 주장·잘못된 code 타입은 기존 `CRITIQUE_INVALID_SUMMARY` 경계에서 차단되고 큐 0개. 입력 ID 오류도 등록 전 차단.
- 조회·반환 객체 변이로 큐 내부를 바꿀 수 없음. 질문·SQL·행·경로·secret형 raw ID가 항목·결과·예외·stdout·기본 로그에 없음.
- 모델/provider/executor/DB/network 호출은 큐 동작에서 0회. 관련 테스트를 먼저 돌리고 마지막에 `.venv/Scripts/python.exe -m pytest tests -q`, `.venv/Scripts/python.exe -m compileall -q backend tests`, `git diff --check` 실행. Windows 임시폴더 ACL 오류는 원 명령 실패와 우회 환경의 성공을 구분해 보고한다.

## 문서·완료

- plan의 Phase 6 본문·실제 완료 범위 체크박스·다음 시작점·변경 이력을 갱신하고 새 계약 결정은 ADR로 남긴다. `docs/next-session-handoff.md`도 갱신한다. 실제 운영 실패 저장·관리자 UI·사람 승인·full loop를 완료 표시하지 않는다.
- 실제 CNU 자료·private bundle·Ollama 호출·PostgreSQL·legacy·모델 점수는 범위 밖이다. 40건·30건 `NEEDS_HUMAN_REVIEW`와 E3/L5·L1/L3 대기 상태를 유지하고 지금 팀 결정을 요청하지 않는다.
- 변경 파일, 테스트, 남은 위험, 코드 품질 징후를 보고한다. Conventional Commit으로 커밋하되 push는 하지 않는다.
